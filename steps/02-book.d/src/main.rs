// SPDX-License-Identifier: Apache-2.0
//! Step 02 Book's driver over the pinned evidencebook crate.
//!
//!   loop-step02-book book   --key PEM --book-id ID --book-dir DIR --out DIR --time RFC3339 [--note TEXT]
//!   loop-step02-book reseal  --key PEM --record FILE
//!   loop-step02-book witness --key PEM --url URL --checkpoint FILE
//!   loop-step02-book verify  --pub PEM --book-id ID --dir DIR [--witnesses FILE]
//!   loop-step02-book lab-receipt --key PEM --statement FILE --at UNIX
//!
//! `book` opens a fresh book, appends two records sealed with the key (an
//! opening record, then the record the step asserts on), cuts a signed
//! checkpoint over both, and writes the proof files into --out:
//! record.json, placement.json, checkpoint.json, checkpoint.cose (when the
//! library built the COSE form) and inclusion.json.
//!
//! `witness` is the rehearsal witness: given a checkpoint (checkpoint.json),
//! it signs the checkpoint's digest with its key and prints the receipt, in the
//! form a CLL witness answers `POST /v1/digest` with plus its `ts_url`: an
//! RFC 9162 COSE Receipt over a one-entry tree whose entry is SHA-256 of the
//! checkpoint digest. It checks nothing; whoever holds the receipt verifies it.
//!
//! `verify` reads only those files and checks them with the library:
//! the record id is recomputed from the record (evidencebook JSON-DIGEST),
//! the record's seal is checked against the public key, the checkpoint's
//! signature and signer are checked, and the record's inclusion at the stated
//! position under the checkpoint root is checked with
//! `evidencebook::substrate::verify_inclusion`. With --witnesses (a witness
//! directory, `witnesses.json`), the receipts checkpoint.json carries are
//! verified against checkpoint.cose by the library's served summary
//! (`served_summary::summary_value`), which counts a checkpoint witnessed only
//! on a receipt AAC's witness verifier passes under that directory. It prints one JSON object
//! and exits 0 when every check held, 1 when any did not, 2 on a usage or
//! input error.
//!
//! `lab-receipt` is the lab test witness's signer (lab/witness.py runs it, on
//! the lab host, with the witness's own key): given a book's raw COSE
//! checkpoint statement it checks the statement's signature with
//! checkpointed-local-log and prints the `POST /checkpoints` response a CLL
//! witness client reads, its receipt naming the key id and the signing time.
//!
//! `reseal` re-signs an edited record.json with the key: tests use it to
//! plant a record rewritten after append by its own producer.
//!
//! A record's seal is an Ed25519 signature over the 32 octets of its record
//! id. These are rehearsal records, not capsules.

use ed25519_dalek::pkcs8::{DecodePrivateKey, DecodePublicKey};
use ed25519_dalek::{Signature, Signer as _, SigningKey, Verifier as _, VerifyingKey};
use evidencebook::canonical::json_digest;
use evidencebook::record::{EpistemicType, Header, HEADER_VERSION};
use evidencebook::served_summary::{summary_value, Coverage, Labels};
use evidencebook::substrate::{
    record_id_from_hex, verify_inclusion, Checkpoint, CllSubstrate, InclusionEvidence, RecordId,
    WitnessEntry, CHECKPOINT_KIND,
};
use serde_json::{json, Value};
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::process::ExitCode;

const RECORD_TYPE: &str = "loop_rehearsal_record";
/// The position the asserted record is appended at: after the opening record.
const POSITION: u64 = 1;

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let result = match args.first().map(String::as_str) {
        Some("book") => flags(&args[1..]).and_then(|f| book(&f)).map(|_| 0),
        Some("reseal") => flags(&args[1..]).and_then(|f| reseal(&f)).map(|_| 0),
        Some("witness") => flags(&args[1..]).and_then(|f| witness(&f)).map(|_| 0),
        Some("verify") => flags(&args[1..]).and_then(|f| verify(&f)),
        Some("lab-receipt") => flags(&args[1..]).and_then(|f| lab_receipt(&f)).map(|_| 0),
        _ => Err("usage: loop-step02-book book|reseal|witness|verify|lab-receipt [flags]; see the source header".into()),
    };
    match result {
        Ok(code) => ExitCode::from(code),
        Err(e) => {
            eprintln!("loop-step02-book: {e}");
            ExitCode::from(2)
        }
    }
}

type Flags = BTreeMap<String, String>;

fn flags(args: &[String]) -> Result<Flags, String> {
    let mut out = Flags::new();
    let mut it = args.iter();
    while let Some(k) = it.next() {
        let name = k.strip_prefix("--").ok_or(format!("unexpected argument {k}"))?;
        let v = it.next().ok_or(format!("{k} needs a value"))?;
        out.insert(name.to_string(), v.clone());
    }
    Ok(out)
}

fn need<'a>(f: &'a Flags, name: &str) -> Result<&'a str, String> {
    f.get(name).map(String::as_str).ok_or(format!("--{name} is required"))
}

fn read(path: &Path) -> Result<Vec<u8>, String> {
    std::fs::read(path).map_err(|e| format!("{}: {e}", path.display()))
}

fn read_json(path: &Path) -> Result<Value, String> {
    serde_json::from_slice(&read(path)?).map_err(|e| format!("{}: {e}", path.display()))
}

fn write(path: &Path, bytes: &[u8]) -> Result<(), String> {
    std::fs::write(path, bytes).map_err(|e| format!("{}: {e}", path.display()))
}

fn write_json(path: &Path, v: &Value) -> Result<(), String> {
    let mut s = serde_json::to_string_pretty(v).map_err(|e| e.to_string())?;
    s.push('\n');
    write(path, s.as_bytes())
}

fn signing_key(path: &str) -> Result<SigningKey, String> {
    let pem = String::from_utf8(read(Path::new(path))?).map_err(|e| e.to_string())?;
    SigningKey::from_pkcs8_pem(&pem).map_err(|e| format!("{path}: not an Ed25519 PKCS#8 key: {e}"))
}

fn verifying_key(path: &str) -> Result<VerifyingKey, String> {
    let pem = String::from_utf8(read(Path::new(path))?).map_err(|e| e.to_string())?;
    VerifyingKey::from_public_key_pem(&pem)
        .map_err(|e| format!("{path}: not an Ed25519 SPKI key: {e}"))
}

/// The record id of a record.json's header, and the header itself.
fn record_id(header: &Value) -> Result<(RecordId, String), String> {
    let hex_id = json_digest(header).map_err(|e| format!("header has no canonical form: {e}"))?;
    let id = record_id_from_hex(&hex_id).ok_or("record id is not 32 octets")?;
    Ok((id, hex_id))
}

fn sealed_record(key: &SigningKey, header: &Header) -> Result<(RecordId, Value), String> {
    header.validate().map_err(|e| e.to_string())?;
    let header = serde_json::to_value(header).map_err(|e| e.to_string())?;
    let (id, _) = record_id(&header)?;
    Ok((id, seal(key, header, &id)))
}

fn seal(key: &SigningKey, header: Value, id: &RecordId) -> Value {
    json!({
        "header": header,
        "seal": {
            "alg": "Ed25519",
            "key_id": hex::encode(key.verifying_key().to_bytes()),
            "signature": hex::encode(key.sign(id).to_bytes()),
        },
    })
}

fn header(book_id: &str, seq: u64, time: &str, note: &str) -> Header {
    Header {
        version: HEADER_VERSION,
        book_id: book_id.to_string(),
        seq,
        record_type: RECORD_TYPE.to_string(),
        epistemic_type: EpistemicType::ProducerClaim,
        committed_at: time.to_string(),
        event_time_claim: None,
        payload_commitments: Vec::new(),
        retention_state: None,
        links: Vec::new(),
        subject_ref: None,
        principal_ref: None,
        counterparty_ref: None,
        correlation: BTreeMap::new(),
        statement: Some(json!({ "note": note })),
    }
}

fn book(f: &Flags) -> Result<(), String> {
    let key = signing_key(need(f, "key")?)?;
    let book_id = need(f, "book-id")?;
    let time = need(f, "time")?;
    let note = f.get("note").map(String::as_str).unwrap_or("rehearsal record");
    let book_dir = PathBuf::from(need(f, "book-dir")?);
    let out = PathBuf::from(need(f, "out")?);
    std::fs::create_dir_all(&book_dir).map_err(|e| e.to_string())?;
    std::fs::create_dir_all(&out).map_err(|e| e.to_string())?;

    let (mut log, _) = CllSubstrate::open(&book_dir.join("nodes.bin"), &book_dir.join("checkpoints.jsonl"))
        .map_err(|e| e.to_string())?;
    if log.leaf_count().map_err(|e| e.to_string())? != 0 {
        return Err(format!("{}: the book is not new", book_dir.display()));
    }
    let (opening_id, _) = sealed_record(&key, &header(book_id, 1, time, "book opened"))?;
    let (id, record) = sealed_record(&key, &header(book_id, 2, time, note))?;
    log.append_leaf(&opening_id).map_err(|e| e.to_string())?;
    let position = log.append_leaf(&id).map_err(|e| e.to_string())?;
    assert_eq!(position, POSITION);

    let prepared = log
        .prepare_checkpoint(position + 1, book_id, time, &key)
        .map_err(|e| e.to_string())?;
    let cose = prepared.cose().map(<[u8]>::to_vec);
    let cp = log.commit_checkpoint(prepared).map_err(|e| e.to_string())?;
    let proof = log.inclusion_at(position, cp.mmr_size).map_err(|e| e.to_string())?;

    write_json(&out.join("record.json"), &record)?;
    write_json(
        &out.join("placement.json"),
        &json!({ "book_id": book_id, "position": position, "record_id": hex::encode(id) }),
    )?;
    write_json(&out.join("checkpoint.json"), &serde_json::to_value(&cp).map_err(|e| e.to_string())?)?;
    write_json(&out.join("inclusion.json"), &proof.to_wire_json())?;
    if let Some(cose) = cose {
        write(&out.join("checkpoint.cose"), &cose)?;
    }
    Ok(())
}

fn reseal(f: &Flags) -> Result<(), String> {
    let key = signing_key(need(f, "key")?)?;
    let path = PathBuf::from(need(f, "record")?);
    let record = read_json(&path)?;
    let header = record.get("header").cloned().ok_or("record.json has no header")?;
    let (id, _) = record_id(&header)?;
    write_json(&path, &seal(&key, header, &id))
}

/// The rehearsal witness's receipt for a checkpoint, as the Rust and Go
/// books' own tests mint one: COSE_Sign1, EdDSA, verifiable data structure
/// RFC 9162 (395 = 1), the inclusion proof [tree size 1, leaf index 0, no
/// path] under 396, and the detached payload the leaf hash of the entry.
fn receipt(cp: &Checkpoint, ts_url: &str, key: &SigningKey) -> Result<WitnessEntry, String> {
    use base64::Engine as _;
    let digest = hex::decode(cp.digest()).map_err(|e| e.to_string())?;
    let (entry, bytes) = signed_receipt(&digest, key, None)?;
    Ok(WitnessEntry {
        ts_url: ts_url.to_string(),
        entry_hash: hex::encode(entry),
        receipt_b64: base64::engine::general_purpose::STANDARD.encode(bytes),
        leaf_index: 0,
        tree_size: 1,
        is_stub: false,
    })
}

/// A one-entry RFC 9162 COSE receipt over a checkpoint digest: (entry hash,
/// tagged COSE_Sign1 bytes). With `at`, the protected header also carries
/// the witness's key id (label 4, the raw Ed25519 public key) and the time
/// it signed (CWT claims, label 15, iat = 6), so a verifier picks the key by
/// its id and checks the time against that key's listing.
fn signed_receipt(digest: &[u8], key: &SigningKey, at: Option<i64>) -> Result<([u8; 32], Vec<u8>), String> {
    use coset::{cbor::value::Value as Cbor, iana, TaggedCborSerializable};
    use sha2::{Digest, Sha256};
    let entry: [u8; 32] = Sha256::digest(digest).into();
    let leaf: [u8; 32] = Sha256::digest([&[0u8][..], &entry].concat()).into();
    let proof = vec![0x83, 0x01, 0x00, 0x80];
    let mut protected = coset::HeaderBuilder::new()
        .algorithm(iana::Algorithm::EdDSA)
        .value(395, Cbor::Integer(1.into()));
    if let Some(at) = at {
        protected = protected
            .key_id(key.verifying_key().to_bytes().to_vec())
            .value(15, Cbor::Map(vec![(Cbor::Integer(6.into()), Cbor::Integer(at.into()))]));
    }
    let mut sign1 = coset::CoseSign1Builder::new()
        .protected(protected.build())
        .unprotected(
            coset::HeaderBuilder::new()
                .value(396, Cbor::Map(vec![(Cbor::Integer((-1).into()), Cbor::Array(vec![Cbor::Bytes(proof)]))]))
                .build(),
        )
        .payload(leaf.to_vec())
        .create_signature(b"", |tbs| key.sign(tbs).to_bytes().to_vec())
        .build();
    sign1.payload = None;
    Ok((entry, sign1.to_tagged_vec().map_err(|e| e.to_string())?))
}

/// The lab test witness's answer to `POST <endpoint>/checkpoints` (lab/witness.py
/// serves it): given the raw COSE checkpoint statement a book delivers, it checks
/// the statement's own signature with checkpointed-local-log, then signs a receipt
/// over the checkpoint's digest with its key, stating its key id and the time.
/// Prints the response body the CLL witness client reads.
fn lab_receipt(f: &Flags) -> Result<(), String> {
    use base64::Engine as _;
    let key = signing_key(need(f, "key")?)?;
    let at: i64 = need(f, "at")?.parse().map_err(|e| format!("--at: {e}"))?;
    let statement = read(Path::new(need(f, "statement")?))?;
    let checked = cll::checkpoint::verify_checkpoint_cose_offline(&statement);
    let decoded = match (checked.ok, checked.decoded) {
        (true, Some(d)) => d,
        _ => return Err(format!("the checkpoint statement does not verify: {}", checked.errors.join("; "))),
    };
    let digest = hex::decode(decoded.to_checkpoint_record().digest()).map_err(|e| e.to_string())?;
    let (entry, bytes) = signed_receipt(&digest, &key, Some(at))?;
    let body = json!({
        "receipt_b64": base64::engine::general_purpose::STANDARD.encode(bytes),
        "entry_hash": hex::encode(entry),
        "entry_hash_scheme": "legacy",
        "leaf_index": 0,
        "tree_size": 1,
    });
    println!("{body}");
    Ok(())
}

fn witness(f: &Flags) -> Result<(), String> {
    let key = signing_key(need(f, "key")?)?;
    let url = need(f, "url")?;
    let path = PathBuf::from(need(f, "checkpoint")?);
    let cp: Checkpoint = serde_json::from_value(read_json(&path)?)
        .map_err(|e| format!("{}: {e}", path.display()))?;
    let entry = receipt(&cp, url, &key)?;
    let v = serde_json::to_value(&entry).map_err(|e| e.to_string())?;
    println!("{}", serde_json::to_string_pretty(&v).map_err(|e| e.to_string())?);
    Ok(())
}

/// The labels the served summary is published under. Only its `coverage`
/// member is read here, so these are fixed rehearsal names.
const LABELS: Labels<'static> = Labels {
    schema: "loop_rehearsal_summary/0",
    definition_digest: "",
    groups_member: "groups",
    digest_member: "digest",
    variants_member: "variants",
    refused_note: "",
    digest_absent_note: "",
    selection_note: "",
    derivation_note: "",
    coverage_note: "",
    adjudications_note: "",
    no_requester_identifiers: "",
    not_a_score: "",
};

/// The `ts_url`s of the receipts the library counts as witnessing `cp`,
/// verified against `statement` under `directory`.
fn verified_witnesses(cp: &Checkpoint, statement: &[u8], directory: &Value) -> Vec<String> {
    let v = summary_value(
        &[],
        &Coverage { checkpoint: cp, covered: 0, statement, witness_directory: Some(directory) },
        &|_| false,
        &LABELS,
    );
    let coverage = &v["coverage"];
    if coverage["witnessed"] != json!(true) {
        return Vec::new();
    }
    coverage["witnesses"]
        .as_array()
        .map(|a| a.iter().filter_map(|w| w.as_str().map(str::to_string)).collect())
        .unwrap_or_default()
}

struct Checks(Vec<Value>);

impl Checks {
    fn add(&mut self, name: &str, ok: bool, detail: impl Into<String>) -> bool {
        self.0.push(json!({ "check": name, "ok": ok, "detail": detail.into() }));
        ok
    }
}

fn verify(f: &Flags) -> Result<u8, String> {
    let alice = verifying_key(need(f, "pub")?)?;
    let alice_hex = hex::encode(alice.to_bytes());
    let book_id = need(f, "book-id")?;
    let dir = PathBuf::from(need(f, "dir")?);
    let record = read_json(&dir.join("record.json"))?;
    let placement = read_json(&dir.join("placement.json"))?;
    let cp: Checkpoint = serde_json::from_value(read_json(&dir.join("checkpoint.json"))?)
        .map_err(|e| format!("checkpoint.json: {e}"))?;
    let proof: InclusionEvidence = serde_json::from_value(read_json(&dir.join("inclusion.json"))?)
        .map_err(|e| format!("inclusion.json: {e}"))?;
    let position = placement["position"].as_u64().ok_or("placement.json: position is not a number")?;

    let mut c = Checks(Vec::new());

    // The record: a valid header, its id recomputed from its bytes, its seal.
    let header_v = record.get("header").cloned().unwrap_or(Value::Null);
    let header_ok = serde_json::from_value::<Header>(header_v.clone())
        .map_err(|e| e.to_string())
        .and_then(|h| h.validate().map_err(|e| e.to_string()).map(|_| h))
        .map(|h| h.book_id == book_id);
    c.add(
        "record_header",
        matches!(header_ok, Ok(true)),
        match &header_ok {
            Ok(true) => format!("a valid evidencebook header in book {book_id}"),
            Ok(false) => format!("the header names another book than {book_id}"),
            Err(e) => e.clone(),
        },
    );
    let (id, id_hex) = record_id(&header_v)?;
    let seal = &record["seal"];
    let sig = hex::decode(seal["signature"].as_str().unwrap_or(""))
        .ok()
        .and_then(|b| Signature::from_slice(&b).ok());
    let seal_ok = seal["key_id"].as_str() == Some(alice_hex.as_str())
        && sig.is_some_and(|s| alice.verify(&id, &s).is_ok());
    c.add(
        "record_seal",
        seal_ok,
        if seal_ok { "sealed by the Alice key over the recomputed record id" } else { "the seal does not verify under the Alice key" },
    );

    // The checkpoint: signed, by the book's key, for this book.
    let sig_ok = cp.verify_signature_offline();
    c.add(
        "checkpoint_signature",
        sig_ok,
        if sig_ok { format!("signature over digest {} verifies", cp.digest()) } else { "the checkpoint signature does not verify".into() },
    );
    let signer_ok = cp.key_id == alice_hex;
    c.add(
        "checkpoint_signer",
        signer_ok,
        if signer_ok { "signed by the Alice key".to_string() } else { format!("signed by {}, not the Alice key", cp.key_id) },
    );
    let book_ok = cp.kind == CHECKPOINT_KIND && cp.log_id == book_id;
    c.add(
        "checkpoint_book",
        book_ok,
        format!("kind {}, log_id {} (want {CHECKPOINT_KIND}, {book_id})", cp.kind, cp.log_id),
    );

    // Inclusion of the recomputed record id at the stated position under the root.
    let root = record_id_from_hex(&cp.root);
    let incl_ok = root.is_some_and(|r| verify_inclusion(&r, cp.mmr_size, position, &id, &proof));
    c.add(
        "inclusion",
        incl_ok,
        if incl_ok {
            format!("record {id_hex} is at position {position} under root {} (mmr_size {})", cp.root, cp.mmr_size)
        } else {
            format!("record {id_hex} is not proved at position {position} under root {} (mmr_size {})", cp.root, cp.mmr_size)
        },
    );

    // The witness receipts: verified by the library against the checkpoint's
    // own COSE statement, under the witness directory.
    let mut witnessed = Vec::new();
    if let Some(dir_path) = f.get("witnesses") {
        let directory = read_json(Path::new(dir_path))?;
        let statement = std::fs::read(dir.join("checkpoint.cose")).unwrap_or_default();
        witnessed = verified_witnesses(&cp, &statement, &directory);
        let ok = !witnessed.is_empty();
        c.add(
            "witness_receipt",
            ok,
            if ok {
                format!("{} of {} receipts on checkpoint {} verify under the witness directory: {}",
                        witnessed.len(), cp.witnesses.len(), cp.digest(), witnessed.join(", "))
            } else {
                format!("none of the {} receipts on checkpoint {} verifies under the witness directory",
                        cp.witnesses.len(), cp.digest())
            },
        );
    }

    let all = c.0.iter().all(|v| v["ok"] == json!(true));
    let out = json!({
        "verifier": "evidencebook (Rust) at action-state-group/evidencebook v0.8.1: canonical::json_digest, Checkpoint::verify_signature_offline, substrate::verify_inclusion, served_summary (AAC's witness receipt verifier)",
        "book_id": book_id,
        "position": position,
        "record_id": id_hex,
        "checkpoint_digest": cp.digest(),
        "witness_receipts": cp.witnesses.len(),
        "witnesses_verified": witnessed,
        "checks": c.0,
        "ok": all,
    });
    println!("{}", serde_json::to_string_pretty(&out).map_err(|e| e.to_string())?);
    Ok(if all { 0 } else { 1 })
}

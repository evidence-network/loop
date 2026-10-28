// SPDX-License-Identifier: Apache-2.0
//! Step 02 Book's driver over the pinned evidencebook crate.
//!
//!   loop-step02-book book   --key PEM --book-id ID --book-dir DIR --out DIR --time RFC3339 [--note TEXT]
//!   loop-step02-book reseal --key PEM --record FILE
//!   loop-step02-book verify --pub PEM --book-id ID --dir DIR
//!
//! `book` opens a fresh book, appends two records sealed with the key (an
//! opening record, then the record the step asserts on), cuts a signed
//! checkpoint over both, and writes the proof files into --out:
//! record.json, placement.json, checkpoint.json, checkpoint.cose (when the
//! library built the COSE form) and inclusion.json.
//!
//! `verify` reads only those files and checks them with the library:
//! the record id is recomputed from the record (evidencebook JSON-DIGEST),
//! the record's seal is checked against the public key, the checkpoint's
//! signature and signer are checked, and the record's inclusion at the stated
//! position under the checkpoint root is checked with
//! `evidencebook::substrate::verify_inclusion`. It prints one JSON object
//! and exits 0 when every check held, 1 when any did not, 2 on a usage or
//! input error.
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
use evidencebook::substrate::{
    record_id_from_hex, verify_inclusion, Checkpoint, CllSubstrate, InclusionEvidence, RecordId,
    CHECKPOINT_KIND,
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
        Some("verify") => flags(&args[1..]).and_then(|f| verify(&f)),
        _ => Err("usage: loop-step02-book book|reseal|verify [flags]; see the source header".into()),
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

    let all = c.0.iter().all(|v| v["ok"] == json!(true));
    let out = json!({
        "verifier": "evidencebook 0.0.7 (Rust): canonical::json_digest, Checkpoint::verify_signature_offline, substrate::verify_inclusion",
        "book_id": book_id,
        "position": position,
        "record_id": id_hex,
        "checkpoint_digest": cp.digest(),
        "witness_receipts": cp.witnesses.len(),
        "checks": c.0,
        "ok": all,
    });
    println!("{}", serde_json::to_string_pretty(&out).map_err(|e| e.to_string())?);
    Ok(if all { 0 } else { 1 })
}

// SPDX-License-Identifier: Apache-2.0
//! Step 05 Deal and push's driver over the pinned evidencebook crate.
//!
//!   loop-step05-deal deal   --a-key PEM --b-key PEM --a-name N --b-name N --population P
//!                           --deal-id ID --time RFC3339 --a-policy V --b-policy V --work DIR --out DIR
//!                           [--declarations FILE] [--a-accept VIA] [--b-accept VIA]
//!                           [--plant wrong-checkpoint]
//!   loop-step05-deal declare --controller-key PEM --evidence-key PEM --time RFC3339 --not-after RFC3339
//!                            --nonce TEXT
//!   loop-step05-deal verify --a-pub PEM --b-pub PEM --a-name N --b-name N --dir DIR
//!   loop-step05-deal held   --record-id HEX --key-id HEX --checkpoint FILE --inclusion FILE
//!
//! `deal` plays both sides of one deal, each in its own book under --work,
//! and writes what each side holds into --out/<name>/:
//!
//! 1. Each side opens its book (an opening record) and cuts a checkpoint,
//!    `checkpoint-open.json`. The two sides learn each other's open
//!    checkpoint before the deal, as a node learns it from a commitment.
//! 2. Each side seals its half of the deal (`half.json`, record type
//!    `deal_half`): the deal id and the exchange's request and response
//!    digests in `correlation`, the other side's key in `counterparty_ref`,
//!    and the other side's open checkpoint (log id, size, digest) in its
//!    statement. It appends the half and cuts a checkpoint over it.
//! 3. At completion, a side whose `share_record_at_completion` is
//!    `counterparty` pushes its half as a bundle (`push.json`:
//!    `{"record_push_bundle": 1, "record", "inclusion": {"leaf_index",
//!    "proof"}, "checkpoint"}`, the
//!    capsules plugin's record-push bundle with an evidencebook record in
//!    place of a capsule). A side with `off` pushes nothing.
//! 4. The other side receives the push. With `off` it refuses
//!    (`policy_decline`). Otherwise it checks the bundle: the record id
//!    recomputes from the header, the seal verifies under the sender's
//!    evidence key, the checkpoint is signed by that key and the record is
//!    included under it. Any failure is a refusal reply naming the refused
//!    key (`refusal.json`, not sealed). A push that passes is held as
//!    transmitted, by its record id, in `held/received-records.jsonl`, with a
//!    provenance line (`held/received-provenance.jsonl`: the sender's key and
//!    `accepted_via`, see below) and the bundle's checkpoint and
//!    proof (`held/received-inclusion.jsonl`), the plugin's three stores. The
//!    receiver then seals a citing record in its own book (`citing.json`,
//!    record type `counterparty_half_citation`): a `cites` link to the
//!    foreign half's record id, and in its statement `citation_purpose:
//!    counterparty_half`, the checkpoint the half was pushed under and the
//!    SHA-256 of the line it holds. It cuts a checkpoint over it
//!    (`checkpoint.json`, `citing-inclusion.json`).
//!
//! `accepted_via` is how the receiver resolved the sender (#69's shape):
//! `controller_declaration` when a declaration in --declarations (each made
//! by `declare`: a controller-binding/0 record both the controller's key and
//! the evidence key sign) verifies and names the sender's evidence key; else `operator_stopgap` (the population names the key). A
//! side set with --<s>-accept `operator_stopgap` or `host_announced` records
//! that instead. The declarations are written to --out/declarations.json.
//!
//! `--plant wrong-checkpoint` makes side A's half cite a checkpoint of
//! another book signed by side B's key instead of B's open checkpoint: a
//! test's planted failure.
//!
//! `verify` reads only those files and checks each side's half, push, held
//! bytes, acceptance and citing record with the library (record ids recomputed with
//! `canonical::json_digest`, checkpoints with
//! `Checkpoint::verify_signature_offline`, inclusion with
//! `substrate::verify_inclusion`, seals with Ed25519). It prints one JSON
//! object and exits 0 when every check held, 1 when any did not, 2 on a
//! usage or input error. A side that accepted other than under a controller
//! declaration is listed in `acceptance_not_checked`, never as a pass; a
//! side whose counterparty's key is bound to its own controller fails
//! `counterparty_distinct`.
//!
//! `held` checks one half a capsules plugin node holds: that `record-id`
//! (a capsule id) is included under the held checkpoint by the held proof,
//! and that the checkpoint is signed by `key-id`. It prints one JSON object, with the checkpoint's
//! recomputed `checkpoint_digest`,
//! and exits like `verify`.
//!
//! A record's seal is an Ed25519 signature over the 32 octets of its record
//! id. These are rehearsal records, not capsules.

use ed25519_dalek::pkcs8::{DecodePrivateKey, DecodePublicKey};
use ed25519_dalek::{Signature, Signer as _, SigningKey, Verifier as _, VerifyingKey};
use evidencebook::canonical::json_digest;
use evidencebook::record::{EpistemicType, Header, Link, LinkType, HEADER_VERSION};
use evidencebook::substrate::{
    record_id_from_hex, verify_inclusion, Checkpoint, CllSubstrate, InclusionEvidence, RecordId,
    CHECKPOINT_KIND,
};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::process::ExitCode;

const HALF_TYPE: &str = "deal_half";
const CITING_TYPE: &str = "counterparty_half_citation";
const CITATION_PURPOSE: &str = "counterparty_half";
const BUNDLE_MARKER: &str = "record_push_bundle";
const POLICY_ON: &str = "counterparty";
/// How a receiver resolved a push's sender (#69's `accepted_via`). Only the
/// first meets the step's acceptance assertion.
const VIA_DECLARATION: &str = "controller_declaration";
const VIA_STOPGAP: &str = "operator_stopgap";
const VIA_HOST: &str = "host_announced";
const ACCEPT_MODES: [&str; 3] = [VIA_DECLARATION, VIA_STOPGAP, VIA_HOST];
/// The exchange both halves record: a rehearsal request and its response.
const REQUEST: &[u8] = br#"{"model":"rehearsal","messages":[{"role":"user","content":"one rehearsal question"}]}"#;
const RESPONSE: &[u8] = br#"{"id":"rehearsal","choices":[{"message":{"role":"assistant","content":"one rehearsal answer"}}]}"#;
const HELD_RECORDS: &str = "received-records.jsonl";
const HELD_PROVENANCE: &str = "received-provenance.jsonl";
const HELD_INCLUSION: &str = "received-inclusion.jsonl";

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let result = match args.first().map(String::as_str) {
        Some("deal") => flags(&args[1..]).and_then(|f| deal(&f)).map(|_| 0),
        Some("declare") => flags(&args[1..]).and_then(|f| declare(&f)).map(|_| 0),
        Some("verify") => flags(&args[1..]).and_then(|f| verify(&f)),
        Some("held") => flags(&args[1..]).and_then(|f| held(&f)),
        _ => Err("usage: loop-step05-deal deal|verify|held [flags]; see the source header".into()),
    };
    match result {
        Ok(code) => ExitCode::from(code),
        Err(e) => {
            eprintln!("loop-step05-deal: {e}");
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

fn write_json(path: &Path, v: &Value) -> Result<(), String> {
    let mut s = serde_json::to_string_pretty(v).map_err(|e| e.to_string())?;
    s.push('\n');
    std::fs::write(path, s).map_err(|e| format!("{}: {e}", path.display()))
}

fn append_line(path: &Path, line: &str) -> Result<(), String> {
    use std::io::Write as _;
    let mut f = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)
        .map_err(|e| format!("{}: {e}", path.display()))?;
    writeln!(f, "{line}").map_err(|e| format!("{}: {e}", path.display()))
}

fn signing_key(path: &str) -> Result<SigningKey, String> {
    let pem = String::from_utf8(read(Path::new(path))?).map_err(|e| e.to_string())?;
    SigningKey::from_pkcs8_pem(&pem).map_err(|e| format!("{path}: not an Ed25519 PKCS#8 key: {e}"))
}

fn verifying_key(path: &str) -> Result<VerifyingKey, String> {
    let pem = String::from_utf8(read(Path::new(path))?).map_err(|e| e.to_string())?;
    VerifyingKey::from_public_key_pem(&pem).map_err(|e| format!("{path}: not an Ed25519 SPKI key: {e}"))
}

fn key_hex(key: &VerifyingKey) -> String {
    hex::encode(key.to_bytes())
}

fn sha256_hex(bytes: &[u8]) -> String {
    hex::encode(Sha256::digest(bytes))
}

/// The record id of a header, recomputed (evidencebook JSON-DIGEST).
fn record_id(header: &Value) -> Result<(RecordId, String), String> {
    let hex_id = json_digest(header).map_err(|e| format!("header has no canonical form: {e}"))?;
    let id = record_id_from_hex(&hex_id).ok_or("record id is not 32 octets")?;
    Ok((id, hex_id))
}

fn seal(key: &SigningKey, header: &Header) -> Result<(RecordId, String, Value), String> {
    header.validate().map_err(|e| e.to_string())?;
    let header = serde_json::to_value(header).map_err(|e| e.to_string())?;
    let (id, id_hex) = record_id(&header)?;
    let record = json!({
        "header": header,
        "seal": {
            "alg": "Ed25519",
            "key_id": hex::encode(key.verifying_key().to_bytes()),
            "signature": hex::encode(key.sign(&id).to_bytes()),
        },
    });
    Ok((id, id_hex, record))
}

fn header(book_id: &str, seq: u64, time: &str, record_type: &str) -> Header {
    Header {
        version: HEADER_VERSION,
        book_id: book_id.to_string(),
        seq,
        record_type: record_type.to_string(),
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
        statement: None,
    }
}

/// One side's book while the deal runs.
struct Book {
    name: String,
    book_id: String,
    key: SigningKey,
    log: CllSubstrate,
    seq: u64,
    time: String,
}

impl Book {
    fn open(name: &str, book_id: &str, key: SigningKey, dir: &Path, time: &str) -> Result<Self, String> {
        std::fs::create_dir_all(dir).map_err(|e| e.to_string())?;
        let (log, _) = CllSubstrate::open(&dir.join("nodes.bin"), &dir.join("checkpoints.jsonl"))
            .map_err(|e| e.to_string())?;
        if log.leaf_count().map_err(|e| e.to_string())? != 0 {
            return Err(format!("{}: the book is not new", dir.display()));
        }
        Ok(Book { name: name.into(), book_id: book_id.into(), key, log, seq: 0, time: time.into() })
    }

    fn next_header(&mut self, record_type: &str) -> Header {
        self.seq += 1;
        header(&self.book_id, self.seq, &self.time, record_type)
    }

    /// Seal and append; the record's position and record.
    fn append(&mut self, h: &Header) -> Result<(u64, String, Value), String> {
        let (id, id_hex, record) = seal(&self.key, h)?;
        let position = self.log.append_leaf(&id).map_err(|e| e.to_string())?;
        Ok((position, id_hex, record))
    }

    fn checkpoint(&mut self) -> Result<Checkpoint, String> {
        let size = self.log.leaf_count().map_err(|e| e.to_string())?;
        let prepared = self
            .log
            .prepare_checkpoint(size, &self.book_id, &self.time, &self.key)
            .map_err(|e| e.to_string())?;
        self.log.commit_checkpoint(prepared).map_err(|e| e.to_string())
    }

    fn inclusion(&self, position: u64, cp: &Checkpoint) -> Result<Value, String> {
        Ok(self.log.inclusion_at(position, cp.mmr_size).map_err(|e| e.to_string())?.to_wire_json())
    }

    fn pub_hex(&self) -> String {
        key_hex(&self.key.verifying_key())
    }
}

fn to_value(cp: &Checkpoint) -> Result<Value, String> {
    serde_json::to_value(cp).map_err(|e| e.to_string())
}

fn cited_checkpoint(cp: &Checkpoint) -> Value {
    json!({ "log_id": cp.log_id, "mmr_size": cp.mmr_size, "digest": cp.digest() })
}

/// The receiver's refusal reply: it names the refused key and is not sealed.
fn refusal(refused_key: &str, push_bytes: &[u8], reason: &str, time: &str) -> Value {
    json!({ "status": "refused", "reason": reason, "key_id": refused_key, "issued_at": time,
            "request_digest": sha256_hex(push_bytes) })
}

/// The JCS (RFC 8785) serialization of a value whose strings are ASCII, as a
/// controller-binding/0 record is: members sorted, no whitespace.
fn jcs(v: &Value) -> String {
    match v {
        Value::Object(m) => {
            let mut keys: Vec<&String> = m.keys().collect();
            keys.sort();
            let body: Vec<String> = keys.iter().map(|k| format!("{}:{}", Value::String((*k).clone()), jcs(&m[*k]))).collect();
            format!("{{{}}}", body.join(","))
        }
        Value::Array(a) => format!("[{}]", a.iter().map(jcs).collect::<Vec<_>>().join(",")),
        other => other.to_string(),
    }
}

/// A controller-binding/0 record's signing input (CB v0 §4): the JCS octets of
/// the record without `sigs`. Its SHA-256 is the binding digest.
fn binding_input(b: &Value) -> Vec<u8> {
    let mut body = b.clone();
    if let Some(m) = body.as_object_mut() {
        m.remove("sigs");
    }
    jcs(&body).into_bytes()
}

/// A declaration is a real controller-binding/0 record (`same_controller_asserted`,
/// `dual_signature/0`, both keys Ed25519): the controller's TEST key and the
/// node's evidence key each sign the same signing input. The record does not
/// say which key is the controller's; the population's controllers.json does.
fn declare(f: &Flags) -> Result<(), String> {
    let controller = signing_key(need(f, "controller-key")?)?;
    let evidence = signing_key(need(f, "evidence-key")?)?;
    let time = need(f, "time")?;
    let mut pair = [(key_hex(&controller.verifying_key()), controller), (key_hex(&evidence.verifying_key()), evidence)];
    pair.sort_by(|a, b| a.0.cmp(&b.0));
    let mut b = json!({ "binding_version": "controller-binding/0", "claim": "same_controller_asserted",
                        "method": "dual_signature/0", "keys": [pair[0].0, pair[1].0], "issued_at": time,
                        "not_before": time, "not_after": need(f, "not-after")?, "revocation": "revocation_statement/0",
                        "nonce": need(f, "nonce")? });
    let input = binding_input(&b);
    b["sigs"] = json!(pair.iter().map(|(k, sk)| json!({ "key_id": k, "sig": hex::encode(sk.sign(&input).to_bytes()) })).collect::<Vec<_>>());
    println!("{}", serde_json::to_string_pretty(&b).map_err(|e| e.to_string())?);
    Ok(())
}

/// The binding digest and the two keys of a controller-binding/0 record whose
/// two Ed25519 signatures verify over its signing input; None otherwise.
fn declaration_ok(d: &Value) -> Option<(String, [String; 2])> {
    if d["binding_version"] != "controller-binding/0" || d["claim"] != "same_controller_asserted" || d["method"] != "dual_signature/0" {
        return None;
    }
    let keys = d["keys"].as_array()?;
    let sigs = d["sigs"].as_array()?;
    if keys.len() != 2 || sigs.len() != 2 || keys[0].as_str()? >= keys[1].as_str()? {
        return None;
    }
    let input = binding_input(d);
    for (k, s) in keys.iter().zip(sigs) {
        if s["key_id"] != *k {
            return None;
        }
        let vk: [u8; 32] = hex::decode(k.as_str()?).ok()?.try_into().ok()?;
        let sig = Signature::from_slice(&hex::decode(s["sig"].as_str()?).ok()?).ok()?;
        VerifyingKey::from_bytes(&vk).ok()?.verify(&input, &sig).ok()?;
    }
    Some((sha256_hex(&input), [keys[0].as_str()?.to_string(), keys[1].as_str()?.to_string()]))
}

/// The (anchor key, binding digest) of each verifying declaration that binds
/// `key`: the anchor is the binding's other key, the controller's.
fn controllers_of(decls: &[Value], key: &str) -> Vec<(String, String)> {
    decls.iter().filter_map(declaration_ok).filter_map(|(digest, keys)| {
        keys.iter().position(|k| k == key).map(|i| (keys[1 - i].clone(), digest))
    }).collect()
}

/// The receiver's checks on a pushed bundle, in the plugin receiver's order:
/// shape, the record's own bytes, its seal under the sender's evidence key,
/// the checkpoint's signer and signature, the record's inclusion.
fn check_push(bundle: &Value, sender: &VerifyingKey) -> Result<(String, Checkpoint, Value), &'static str> {
    if bundle.get(BUNDLE_MARKER) != Some(&json!(1)) {
        return Err("bundle_malformed");
    }
    let record = bundle.get("record").ok_or("bundle_malformed")?;
    let header_v = record.get("header").cloned().ok_or("request_malformed")?;
    let header: Header = serde_json::from_value(header_v.clone()).map_err(|_| "request_malformed")?;
    header.validate().map_err(|_| "request_malformed")?;
    let (id, id_hex) = record_id(&header_v).map_err(|_| "request_malformed")?;
    if !seal_ok(record, &id, sender) {
        return Err("signature_unverified");
    }
    let cp: Checkpoint = serde_json::from_value(bundle["checkpoint"].clone()).map_err(|_| "bundle_malformed")?;
    let proof: InclusionEvidence =
        serde_json::from_value(bundle["inclusion"]["proof"].clone()).map_err(|_| "bundle_malformed")?;
    if bundle["inclusion"]["leaf_index"].as_u64() != Some(proof.leaf_index) {
        return Err("bundle_malformed");
    }
    if cp.key_id != key_hex(sender) || !cp.verify_signature_offline() {
        return Err("inclusion_unverified");
    }
    let root = record_id_from_hex(&cp.root).ok_or("inclusion_unverified")?;
    if !verify_inclusion(&root, cp.mmr_size, proof.leaf_index, &id, &proof) {
        return Err("inclusion_unverified");
    }
    Ok((id_hex, cp, bundle["inclusion"]["proof"].clone()))
}

fn seal_ok(record: &Value, id: &RecordId, key: &VerifyingKey) -> bool {
    let seal = &record["seal"];
    let sig = hex::decode(seal["signature"].as_str().unwrap_or(""))
        .ok()
        .and_then(|b| Signature::from_slice(&b).ok());
    seal["key_id"].as_str() == Some(key_hex(key).as_str()) && sig.is_some_and(|s| key.verify(id, &s).is_ok())
}

struct Side {
    book: Book,
    policy: String,
    accept: String,
    out: PathBuf,
    open: Checkpoint,
    half_id: String,
    push: Option<Vec<u8>>,
}

fn deal(f: &Flags) -> Result<(), String> {
    let population = need(f, "population")?;
    let deal_id = need(f, "deal-id")?;
    let time = need(f, "time")?;
    let work = PathBuf::from(need(f, "work")?);
    let out = PathBuf::from(need(f, "out")?);
    let plant = f.get("plant").map(String::as_str);
    if let Some(p) = plant {
        if p != "wrong-checkpoint" {
            return Err(format!("unknown --plant {p}"));
        }
    }
    std::fs::create_dir_all(&out).map_err(|e| e.to_string())?;
    let decls: Vec<Value> = match f.get("declarations") {
        Some(p) => read_json(Path::new(p))?.as_array().cloned().ok_or("--declarations is a JSON array")?,
        None => Vec::new(),
    };
    write_json(&out.join("declarations.json"), &Value::Array(decls.clone()))?;
    let request_digest = sha256_hex(REQUEST);
    let response_digest = sha256_hex(RESPONSE);
    write_json(
        &out.join("deal.json"),
        &json!({ "deal_id": deal_id, "request_digest": request_digest, "response_digest": response_digest,
                 "requester": need(f, "a-name")?, "provider": need(f, "b-name")? }),
    )?;

    // 1. Each side opens its book and cuts its open checkpoint.
    let mut sides = Vec::new();
    for s in ["a", "b"] {
        let name = need(f, &format!("{s}-name"))?.to_string();
        let key = signing_key(need(f, &format!("{s}-key"))?)?;
        let book_id = format!("{population}-{name}-deal");
        let mut book = Book::open(&name, &book_id, key, &work.join(&name), time)?;
        let h = book.next_header("book_opened");
        book.append(&h)?;
        let open = book.checkpoint()?;
        let side_out = out.join(&name);
        std::fs::create_dir_all(side_out.join("held")).map_err(|e| e.to_string())?;
        write_json(&side_out.join("checkpoint-open.json"), &to_value(&open)?)?;
        let accept = f.get(&format!("{s}-accept")).cloned().unwrap_or_else(|| VIA_DECLARATION.to_string());
        if !ACCEPT_MODES.contains(&accept.as_str()) {
            return Err(format!("--{s}-accept is one of {}", ACCEPT_MODES.join(", ")));
        }
        write_json(&side_out.join("settings.json"), &json!({ "share_record_at_completion": need(f, &format!("{s}-policy"))?,
                                                            "accepted_via": accept }))?;
        sides.push(Side {
            book,
            policy: need(f, &format!("{s}-policy"))?.to_string(),
            accept,
            out: side_out,
            open,
            half_id: String::new(),
            push: None,
        });
    }

    // The checkpoint each half cites as the other side's: the other side's
    // open checkpoint, or, planted, one from another book under B's key.
    let mut cites = vec![sides[1].open.clone(), sides[0].open.clone()];
    if plant == Some("wrong-checkpoint") {
        let mut other = Book::open(
            &sides[1].book.name,
            &format!("{population}-{}-other", sides[1].book.name),
            sides[1].book.key.clone(),
            &work.join("other"),
            time,
        )?;
        let h = other.next_header("book_opened");
        other.append(&h)?;
        cites[0] = other.checkpoint()?;
    }

    // 2. Each side seals its half, citing the other side's checkpoint, and
    // 3. pushes it at completion when its setting is `counterparty`.
    let keys = [sides[1].book.pub_hex(), sides[0].book.pub_hex()];
    for (i, side) in sides.iter_mut().enumerate() {
        let role = if i == 0 { "requester" } else { "provider" };
        let mut h = side.book.next_header(HALF_TYPE);
        h.counterparty_ref = Some(keys[i].clone());
        h.correlation.insert("deal_id".into(), deal_id.into());
        h.correlation.insert("request_digest".into(), request_digest.clone());
        h.correlation.insert("response_digest".into(), response_digest.clone());
        h.statement = Some(json!({ "role": role, "counterparty_checkpoint": cited_checkpoint(&cites[i]) }));
        let (position, id_hex, record) = side.book.append(&h)?;
        let cp = side.book.checkpoint()?;
        let inclusion = side.book.inclusion(position, &cp)?;
        write_json(&side.out.join("half.json"), &record)?;
        write_json(&side.out.join("half-checkpoint.json"), &to_value(&cp)?)?;
        write_json(&side.out.join("half-inclusion.json"), &inclusion)?;
        side.half_id = id_hex;
        if side.policy == POLICY_ON {
            let bundle = json!({ BUNDLE_MARKER: 1, "record": record,
                                 "inclusion": { "leaf_index": position, "proof": inclusion },
                                 "checkpoint": to_value(&cp)? });
            let mut bytes = serde_json::to_vec_pretty(&bundle).map_err(|e| e.to_string())?;
            bytes.push(b'\n');
            std::fs::write(side.out.join("push.json"), &bytes).map_err(|e| e.to_string())?;
            side.push = Some(bytes);
        }
    }

    // 4. Each side receives the other's push, holds it and cites it.
    for (i, j) in [(1usize, 0usize), (0, 1)] {
        let Some(bytes) = sides[j].push.clone() else { continue };
        let sender = sides[j].book.key.verifying_key();
        let sender_name = sides[j].book.name.clone();
        let rx = &mut sides[i];
        let decided = if rx.policy != POLICY_ON {
            Err("policy_decline")
        } else {
            serde_json::from_slice::<Value>(&bytes)
                .map_err(|_| "request_malformed")
                .and_then(|b| check_push(&b, &sender).map(|ok| (b, ok)))
        };
        let (bundle, (foreign_id, foreign_cp, proof)) = match decided {
            Ok(v) => v,
            Err(reason) => {
                write_json(&rx.out.join("refusal.json"), &refusal(&key_hex(&sender), &bytes, reason, time))?;
                continue;
            }
        };
        // How the receiver resolved the sender: through a controller
        // declaration binding its evidence key when one does, else through
        // the operator's stopgap list (the population names the key), or as
        // the host announced it when the receiver is set to.
        let bound = controllers_of(&decls, &key_hex(&sender));
        let via = match rx.accept.as_str() {
            VIA_DECLARATION if !bound.is_empty() => VIA_DECLARATION,
            VIA_DECLARATION => VIA_STOPGAP,
            m => m,
        };
        let (anchor, digest) = match bound.first() {
            Some((a, d)) if via == VIA_DECLARATION => (json!(a), json!(d)),
            _ => (Value::Null, Value::Null),
        };
        let line = serde_json::to_string(&bundle["record"]).map_err(|e| e.to_string())?;
        let held = rx.out.join("held");
        append_line(&held.join(HELD_RECORDS), &line)?;
        append_line(
            &held.join(HELD_PROVENANCE),
            &json!({ "record_id": foreign_id, "received_from": format!("{sender_name}-transport"),
                     "key_id": key_hex(&sender), "accepted_via": via,
                     "controller_anchor_key_id": anchor, "controller_binding_digest": digest,
                     "via": "push", "received_at": time,
                     "signature_ok": true })
            .to_string(),
        )?;
        append_line(
            &held.join(HELD_INCLUSION),
            &json!({ "record_id": foreign_id, "leaf_index": proof["leaf_index"], "mmr_size": foreign_cp.mmr_size,
                     "checkpoint_digest": foreign_cp.digest(), "checkpoint": to_value(&foreign_cp)?,
                     "inclusion_proof": proof })
            .to_string(),
        )?;
        let mut h = rx.book.next_header(CITING_TYPE);
        h.links.push(Link { link_type: LinkType::Cites, target: foreign_id.clone() });
        h.counterparty_ref = Some(key_hex(&sender));
        h.correlation.insert("deal_id".into(), deal_id.into());
        h.statement = Some(json!({
            "citation_purpose": CITATION_PURPOSE,
            "cited_record_id": foreign_id,
            "cited_checkpoint": cited_checkpoint(&foreign_cp),
            "held_sha256": sha256_hex(line.as_bytes()),
            "via": "push",
        }));
        let (position, _, record) = rx.book.append(&h)?;
        let cp = rx.book.checkpoint()?;
        write_json(&rx.out.join("citing.json"), &record)?;
        write_json(&rx.out.join("checkpoint.json"), &to_value(&cp)?)?;
        write_json(&rx.out.join("citing-inclusion.json"), &rx.book.inclusion(position, &cp)?)?;
    }
    for side in &sides {
        write_json(
            &side.out.join("placement.json"),
            &json!({ "book_id": side.book.book_id, "half_record_id": side.half_id, "records": side.book.seq }),
        )?;
    }
    Ok(())
}

struct Checks(Vec<Value>);

impl Checks {
    fn add(&mut self, side: &str, name: &str, ok: bool, detail: impl Into<String>) -> bool {
        self.0.push(json!({ "side": side, "check": name, "ok": ok, "detail": detail.into() }));
        ok
    }
}

/// What one side's directory holds, read leniently: a missing file is None.
struct Held {
    dir: PathBuf,
}

impl Held {
    fn json(&self, name: &str) -> Option<Value> {
        read_json(&self.dir.join(name)).ok()
    }
    fn lines(&self, name: &str) -> Vec<String> {
        std::fs::read_to_string(self.dir.join("held").join(name))
            .map(|s| s.lines().map(str::to_string).collect())
            .unwrap_or_default()
    }
}

/// A checkpoint that verifies, signed by `key`, for `book_id` (None: any book).
fn checkpoint_ok(cp: &Value, key: &str, book_id: Option<&str>) -> Result<Checkpoint, String> {
    let cp: Checkpoint = serde_json::from_value(cp.clone()).map_err(|e| format!("not a checkpoint: {e}"))?;
    if cp.kind != CHECKPOINT_KIND {
        return Err(format!("kind {}", cp.kind));
    }
    if !cp.verify_signature_offline() {
        return Err(format!("the signature on checkpoint {} does not verify", cp.digest()));
    }
    if cp.key_id != key {
        return Err(format!("checkpoint {} is signed by {}, not {key}", cp.digest(), cp.key_id));
    }
    if let Some(b) = book_id {
        if cp.log_id != b {
            return Err(format!("checkpoint {} is of {}, not {b}", cp.digest(), cp.log_id));
        }
    }
    Ok(cp)
}

/// The record's id recomputed, its header valid, its seal under `key`.
fn record_ok(record: &Value, key: &VerifyingKey) -> Result<(Header, String), String> {
    let header_v = record.get("header").cloned().ok_or("no header")?;
    let h: Header = serde_json::from_value(header_v.clone()).map_err(|e| format!("not a header: {e}"))?;
    h.validate().map_err(|e| e.to_string())?;
    let (id, id_hex) = record_id(&header_v)?;
    if !seal_ok(record, &id, key) {
        return Err(format!("the seal on record {id_hex} does not verify under {}", key_hex(key)));
    }
    Ok((h, id_hex))
}

fn included(record_id_hex: &str, cp: &Checkpoint, proof: &Value) -> bool {
    let proof: Option<InclusionEvidence> = serde_json::from_value(proof.clone()).ok();
    match (record_id_from_hex(&cp.root), record_id_from_hex(record_id_hex), proof) {
        (Some(root), Some(id), Some(p)) => verify_inclusion(&root, cp.mmr_size, p.leaf_index, &id, &p),
        _ => false,
    }
}

fn verify(f: &Flags) -> Result<u8, String> {
    let dir = PathBuf::from(need(f, "dir")?);
    let deal = read_json(&dir.join("deal.json"))?;
    let names = [need(f, "a-name")?.to_string(), need(f, "b-name")?.to_string()];
    let keys = [verifying_key(need(f, "a-pub")?)?, verifying_key(need(f, "b-pub")?)?];
    let hexes = [key_hex(&keys[0]), key_hex(&keys[1])];
    let deal_id = deal["deal_id"].as_str().unwrap_or_default().to_string();
    let decls: Vec<Value> = read_json(&dir.join("declarations.json")).ok().and_then(|v| v.as_array().cloned()).unwrap_or_default();
    let mut unaccepted: Vec<Value> = Vec::new();
    let mut c = Checks(Vec::new());
    let mut halves: Vec<Option<(Header, String)>> = vec![None, None];
    let mut books = [String::new(), String::new()];

    // Each side's own half, in its own book.
    for i in 0..2 {
        let (name, other) = (&names[i], 1 - i);
        let side = Held { dir: dir.join(name) };
        let half = side.json("half.json").unwrap_or(Value::Null);
        let r = record_ok(&half, &keys[i]);
        let ok = matches!(&r, Ok((h, _)) if h.record_type == HALF_TYPE);
        c.add(name, "half_sealed", ok, match &r {
            Ok((h, id)) => format!("{name}'s half {id} ({}) in book {}, sealed by {name}'s key", h.record_type, h.book_id),
            Err(e) => format!("{name}'s half: {e}"),
        });
        if let Ok((h, id)) = r {
            books[i] = h.book_id.clone();
            let cp = side.json("half-checkpoint.json").unwrap_or(Value::Null);
            let incl = match checkpoint_ok(&cp, &hexes[i], Some(&h.book_id)) {
                Ok(cp) => included(&id, &cp, &side.json("half-inclusion.json").unwrap_or(Value::Null)),
                Err(_) => false,
            };
            c.add(name, "half_in_book", incl, format!("half {id} included under {name}'s checkpoint of {}", h.book_id));
            let corr = |k: &str| h.correlation.get(k).cloned().unwrap_or_default();
            let ok = corr("deal_id") == deal_id
                && corr("request_digest") == deal["request_digest"].as_str().unwrap_or_default()
                && corr("response_digest") == deal["response_digest"].as_str().unwrap_or_default()
                && h.counterparty_ref.as_deref() == Some(hexes[other].as_str());
            c.add(name, "half_names_the_deal", ok, format!(
                "deal {}, request {}, response {}, counterparty {}",
                corr("deal_id"), corr("request_digest"), corr("response_digest"),
                h.counterparty_ref.clone().unwrap_or_default()));
            halves[i] = Some((h, id));
        }
    }

    // Each half cites the other side's checkpoint: the open checkpoint the
    // other side holds, signed by the other side's key for its book.
    for i in 0..2 {
        let (name, other) = (&names[i], 1 - i);
        let Some((h, _)) = &halves[i] else { continue };
        let theirs = Held { dir: dir.join(&names[other]) }.json("checkpoint-open.json").unwrap_or(Value::Null);
        let cited = h.statement.as_ref().map(|s| s["counterparty_checkpoint"].clone()).unwrap_or(Value::Null);
        let (ok, detail) = match checkpoint_ok(&theirs, &hexes[other], Some(&books[other])) {
            Ok(cp) => (
                cited["digest"].as_str() == Some(cp.digest().as_str()) && cited["log_id"].as_str() == Some(cp.log_id.as_str()),
                format!("{name}'s half cites checkpoint {} of {}; {}'s checkpoint is {} of {}",
                        cited["digest"].as_str().unwrap_or("none"), cited["log_id"].as_str().unwrap_or("none"),
                        names[other], cp.digest(), cp.log_id),
            ),
            Err(e) => (false, format!("{}'s open checkpoint: {e}", names[other])),
        };
        c.add(name, "half_cites_counterparty_checkpoint", ok, detail);
    }

    // The two halves are of one exchange, from the two sides of it.
    if let (Some((a, _)), Some((b, _))) = (&halves[0], &halves[1]) {
        let role = |h: &Header| h.statement.as_ref().and_then(|s| s["role"].as_str().map(str::to_string)).unwrap_or_default();
        let ok = a.correlation == b.correlation && role(a) != role(b);
        c.add("both", "halves_of_one_exchange", ok, format!(
            "{} is the {}, {} the {}; correlation {}", names[0], role(a), names[1], role(b),
            if a.correlation == b.correlation { "equal" } else { "differs" }));
    }

    // Each side pushed its half at completion, with its setting on.
    for i in 0..2 {
        let name = &names[i];
        let side = Held { dir: dir.join(name) };
        let setting = side.json("settings.json").and_then(|s| s["share_record_at_completion"].as_str().map(str::to_string));
        c.add(name, "share_record_at_completion", setting.as_deref() == Some(POLICY_ON),
              format!("{name}: share_record_at_completion {}", setting.as_deref().unwrap_or("unset (off in the pinned plugin)")));
        let push = side.json("push.json");
        let (ok, detail) = match (&push, &halves[i]) {
            (None, _) => (false, format!("{name} pushed nothing")),
            (Some(_), None) => (false, format!("{name}'s half did not check, so its push cannot match it")),
            (Some(p), Some((_, id))) => match check_push(p, &keys[i]) {
                Ok((pid, cp, _)) => (&pid == id, format!(
                    "{name} pushed record {pid} under its checkpoint {} (mmr_size {}); its half is {id}", cp.digest(), cp.mmr_size)),
                Err(reason) => (false, format!("{name}'s push does not check: {reason}")),
            },
        };
        c.add(name, "pushed_at_completion", ok, detail);
    }

    // The receiver holds the foreign bytes by digest and seals a record
    // citing them, and the checkpoint they were pushed under.
    for i in 0..2 {
        let (name, other) = (&names[i], 1 - i);
        let side = Held { dir: dir.join(name) };
        let Some((_, foreign_id)) = &halves[other] else {
            c.add(name, "holds_foreign_half", false, format!("{}'s half did not check", names[other]));
            continue;
        };
        let records = side.lines(HELD_RECORDS);
        let prov = side.lines(HELD_PROVENANCE).iter().filter_map(|l| serde_json::from_str::<Value>(l).ok())
            .find(|p| p["record_id"].as_str() == Some(foreign_id.as_str()));
        let held_line = records.iter().find(|l| {
            serde_json::from_str::<Value>(l).ok()
                .and_then(|r| r.get("header").cloned())
                .is_some_and(|h| json_digest(&h).ok().as_deref() == Some(foreign_id.as_str()))
        });
        let pushed_cp = Held { dir: dir.join(&names[other]) }.json("push.json").map(|p| p["checkpoint"].clone());
        let pushed_digest = pushed_cp.and_then(|cp| serde_json::from_value::<Checkpoint>(cp).ok()).map(|cp| cp.digest());
        let (ok, detail) = match (&prov, held_line) {
            (None, _) => (false, format!("{name} holds no provenance for {}'s half {foreign_id}", names[other])),
            (Some(_), None) => (false, format!(
                "{name}'s provenance names {}'s half {foreign_id}, but none of the {} held records recomputes to it",
                names[other], records.len())),
            (Some(p), Some(line)) => {
                let rec: Value = serde_json::from_str(line).unwrap_or(Value::Null);
                let sealed = record_ok(&rec, &keys[other]).is_ok();
                let on_key = p["key_id"].as_str() == Some(hexes[other].as_str());
                (sealed && on_key, format!(
                    "{name} holds {}'s half as transmitted: its bytes recompute to {foreign_id}, {} under {}'s key; \
                     the provenance names evidence key {} (received from {})",
                    names[other], if sealed { "sealed" } else { "NOT sealed" }, names[other],
                    p["key_id"].as_str().unwrap_or("none"), p["received_from"].as_str().unwrap_or("none")))
            }
        };
        c.add(name, "holds_foreign_half", ok, detail);

        // Acceptance: keyed on the sender's evidence key under a controller
        // declaration that verifies and binds it. Accepted any other way, it
        // is not checked here (#69); never a pass.
        if let Some(p) = &prov {
            let via = p["accepted_via"].as_str().unwrap_or("none");
            if via == VIA_DECLARATION {
                let cid = p["controller_anchor_key_id"].as_str().unwrap_or("");
                let digest = p["controller_binding_digest"].as_str().unwrap_or("");
                let ok = controllers_of(&decls, &hexes[other]).iter().any(|(a, d)| a == cid && d == digest);
                c.add(name, "accepted_under_controller_declaration", ok, format!(
                    "{name} accepted {}'s half on evidence key {} under the binding {digest} anchored on {cid}: {}", names[other], hexes[other],
                    if ok { "that binding verifies (both signatures) and names the key" } else { "no verifying binding with that digest and anchor names the key" }));
            } else {
                unaccepted.push(json!({ "side": name, "accepted_via": via }));
            }
        }
        // A counterparty bound to the receiver's own controller is not a
        // different counterparty.
        let own: Vec<String> = controllers_of(&decls, &hexes[i]).into_iter().map(|(a, _)| a).collect();
        let theirs: Vec<String> = controllers_of(&decls, &hexes[other]).into_iter().map(|(a, _)| a).collect();
        // A key counts under its own bindings and as itself: a binding of the two sides' own keys says they are one controller.
        let (own_all, theirs_all) = ([own.clone(), vec![hexes[i].clone()]].concat(), [theirs.clone(), vec![hexes[other].clone()]].concat());
        let shared: Vec<&String> = own_all.iter().filter(|c| theirs_all.contains(c)).collect();
        c.add(name, "counterparty_distinct", shared.is_empty(), format!(
            "{name}'s key is bound to {own:?}, {}'s to {theirs:?}{}", names[other],
            if shared.is_empty() { String::new() } else { format!("; both under {shared:?}, so not a different counterparty") }));

        let citing = side.json("citing.json").unwrap_or(Value::Null);
        let (ok, detail) = match record_ok(&citing, &keys[i]) {
            Err(e) => (false, format!("{name}'s citing record: {e}")),
            Ok((h, cid)) => {
                let s = h.statement.clone().unwrap_or(Value::Null);
                let links = h.links_to(LinkType::Cites);
                let held_sha = held_line.map(|l| sha256_hex(l.as_bytes()));
                let in_book = checkpoint_ok(&side.json("checkpoint.json").unwrap_or(Value::Null), &hexes[i], Some(&books[i]))
                    .map(|cp| included(&cid, &cp, &side.json("citing-inclusion.json").unwrap_or(Value::Null)))
                    .unwrap_or(false);
                let ok = h.record_type == CITING_TYPE
                    && h.book_id == books[i]
                    && links == [foreign_id.as_str()]
                    && s["citation_purpose"] == json!(CITATION_PURPOSE)
                    && s["cited_record_id"].as_str() == Some(foreign_id.as_str())
                    && s["cited_checkpoint"]["digest"].as_str().is_some()
                    && s["cited_checkpoint"]["digest"].as_str() == pushed_digest.as_deref()
                    && held_sha.is_some()
                    && s["held_sha256"].as_str() == held_sha.as_deref()
                    && in_book;
                (ok, format!(
                    "{name}'s record {cid} in {} cites {:?} ({}) under {}'s checkpoint {}, held line sha256 {}; \
                     pushed under {}, held line {}; in {name}'s book: {in_book}",
                    h.book_id, links, s["citation_purpose"].as_str().unwrap_or("none"), names[other],
                    s["cited_checkpoint"]["digest"].as_str().unwrap_or("none"), s["held_sha256"].as_str().unwrap_or("none"),
                    pushed_digest.as_deref().unwrap_or("none"), held_sha.as_deref().unwrap_or("none")))
            }
        };
        c.add(name, "cites_foreign_half", ok, detail);
    }

    let all = c.0.iter().all(|v| v["ok"] == json!(true));
    let out = json!({
        "verifier": "evidencebook (Rust) at action-state-group/evidencebook v0.8.1: canonical::json_digest, Checkpoint::verify_signature_offline, substrate::verify_inclusion; Ed25519 seals",
        "deal_id": deal_id,
        "books": books,
        "halves": halves.iter().map(|h| h.as_ref().map(|(_, id)| id.clone())).collect::<Vec<_>>(),
        "checks": c.0,
        "acceptance_not_checked": unaccepted,
        "ok": all,
    });
    println!("{}", serde_json::to_string_pretty(&out).map_err(|e| e.to_string())?);
    Ok(if all { 0 } else { 1 })
}

fn held(f: &Flags) -> Result<u8, String> {
    let id = need(f, "record-id")?;
    let key = need(f, "key-id")?;
    let cp = read_json(Path::new(need(f, "checkpoint")?))?;
    let proof = read_json(Path::new(need(f, "inclusion")?))?;
    let (ok, detail, digest) = match checkpoint_ok(&cp, key, None) {
        Ok(cp) => {
            let ok = included(id, &cp, &proof);
            (ok, format!("{id} {} under checkpoint {} of {} (mmr_size {}), signed by {key}",
                         if ok { "is included" } else { "is NOT included" }, cp.digest(), cp.log_id, cp.mmr_size),
             Some(cp.digest()))
        }
        Err(e) => (false, e, None),
    };
    // checkpoint_digest: the digest recomputed from the checkpoint itself, which the caller compares to the one a
    // record cites.
    println!("{}", serde_json::to_string_pretty(&json!({ "record_id": id, "ok": ok, "detail": detail,
                                                          "checkpoint_digest": digest })).map_err(|e| e.to_string())?);
    Ok(if ok { 0 } else { 1 })
}

//! `loop-ledger-check --file <copy> --checkpoint <reply.json> --key <node key id> [--from <i> --to <j>]`
//!
//! The check `capsules ledger-check` (capsules-plugin docs/LEDGER-CHECKPOINTS.md) makes, with the same library
//! calls (checkpointed-local-log: the checkpoint's COSE statement and signature, then `verify_range` over the
//! copy's leaf digests), over a copy the loop kept. The loop never keeps a payment preimage, so a line that held
//! one is kept with its preimage value replaced and an `x-loop-redacted` member (steps/live.py copy_redacted);
//! such a line no longer hashes to its `capsule_id`, so the leaf digest of a line is taken as the node's leaf
//! rule gives it from what the copy states:
//!   * `capsules.jsonl` (leaf `capsule_id`): the `capsule_id` the line states (not recomputed from the record,
//!     which `capsules ledger-check` does and a redacted record cannot);
//!   * the other files (leaf `sha256-line`): the SHA-256 of the line's bytes, or, for a redacted line, the
//!     `line_sha256` its marker names (the SHA-256 of the line as the node holds it).
//! Exit 0 when the copy checks (a JSON object on stdout), 1 when it does not (the reason on stderr), 2 for usage.
use cll::checkpoint::{verify_checkpoint_cose_offline, CheckpointRecord};
use cll::mmr::{leaf_count, Hash};
use cll::range_proof::{verify_range, RangeProof};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

const USAGE: &str = "usage: loop-ledger-check --file <copy> --checkpoint <reply.json> --key <node key id> [--from <i> --to <j>]";
/// What steps/live.py puts in place of a withheld preimage value.
const REDACTED: &str = "redacted: a payment preimage never leaves the lab host";
const CHAIN: &str = "capsules.jsonl";
const FILES: [&str; 6] = [
    "received-capsules.jsonl",
    "received-provenance.jsonl",
    "received-inclusion.jsonl",
    "issued-adjudications.jsonl",
    "received-adjudications.jsonl",
    "requested-adjudications.jsonl",
];

fn hex_hash(text: &str) -> Option<Hash> {
    hex::decode(text).ok()?.try_into().ok()
}

fn proof_from_json(value: &Value) -> Option<RangeProof> {
    let obj = value.as_object()?;
    if obj.len() != 6 {
        return None;
    }
    Some(RangeProof {
        v: u32::try_from(obj.get("v")?.as_u64()?).ok()?,
        kind: obj.get("kind")?.as_str()?.to_string(),
        size: obj.get("size")?.as_u64()?,
        from_index: obj.get("from_index")?.as_u64()?,
        to_index: obj.get("to_index")?.as_u64()?,
        witness: obj
            .get("witness")?
            .as_array()?
            .iter()
            .map(|w| w.as_str().map(str::to_string))
            .collect::<Option<_>>()?,
    })
}

/// (the complete lines, whether a fragment with no newline follows them)
fn complete_lines(bytes: &[u8]) -> (Vec<&[u8]>, bool) {
    let mut lines: Vec<&[u8]> = bytes.split(|&b| b == b'\n').collect();
    let tail = lines.pop().unwrap_or_default();
    (lines, !tail.is_empty())
}

/// (digest, whether the line is a redacted one)
fn leaf(chain: bool, index: usize, line: &[u8]) -> Result<(Hash, bool), String> {
    let parsed: Option<Value> = serde_json::from_slice(line).ok();
    // A marker counts only on a line that says it withheld a payment preimage (steps/live.py REDACTED).
    let withheld = std::str::from_utf8(line).map_or(false, |t| t.contains(REDACTED));
    let marker = parsed
        .as_ref()
        .filter(|_| withheld)
        .and_then(|v| v.get("x-loop-redacted")?.get("line_sha256")?.as_str().map(str::to_string));
    if chain {
        let id = parsed
            .as_ref()
            .and_then(|v| v.get("capsule_id")?.as_str().and_then(hex_hash))
            .ok_or_else(|| format!("copied line {} states no capsule_id", index + 1))?;
        return Ok((id, marker.is_some()));
    }
    match marker {
        Some(m) => hex_hash(&m)
            .map(|h| (h, true))
            .ok_or_else(|| format!("copied line {} names a line_sha256 that is not a digest", index + 1)),
        None => Ok((Sha256::digest(line).into(), false)),
    }
}

fn check(copy: &[u8], reply: &Value, key_id: &str, range: Option<(u64, u64)>) -> Result<Value, String> {
    if reply.get("ledger_checkpoint").and_then(Value::as_u64) != Some(1) {
        return Err("not a ledger_checkpoint reply of version 1".into());
    }
    let file = reply.get("file").and_then(Value::as_str).ok_or("the reply names no file")?;
    let chain = file == CHAIN;
    if !chain && !FILES.contains(&file) {
        return Err(format!("{file:?} is not a checkpointed ledger file"));
    }
    let rule = if chain { "capsule_id" } else { "sha256-line" };
    if reply.get("leaf_digest").and_then(Value::as_str) != Some(rule) {
        return Err(format!("{file}'s leaves are digested as {rule}"));
    }
    let cose = reply
        .get("checkpoint_cose")
        .and_then(Value::as_str)
        .and_then(|h| hex::decode(h).ok())
        .ok_or("the reply carries no checkpoint statement")?;
    let cp: CheckpointRecord = serde_json::from_value(reply.get("checkpoint").cloned().unwrap_or_default())
        .map_err(|e| format!("the reply's checkpoint is malformed: {e}"))?;
    if cp.key_id != key_id {
        return Err(format!("the checkpoint is signed by {}, not by the given key", cp.key_id));
    }
    let verified = verify_checkpoint_cose_offline(&cose);
    let stated = verified
        .decoded
        .filter(|_| verified.ok)
        .map(|d| d.key_id == cp.key_id && d.to_checkpoint_record().signing_body() == cp.signing_body())
        .unwrap_or(false);
    if !stated || !cp.verify_signature_offline() {
        return Err("the checkpoint statement does not verify, or does not state the reply's checkpoint".into());
    }
    let in_file_log = if chain {
        !cp.log_id.contains("/ledger/")
    } else {
        cp.log_id.ends_with(&format!("/ledger/{file}"))
    };
    if !in_file_log {
        return Err(format!("the checkpoint's log {} is not {file}'s", cp.log_id));
    }
    let proof = reply.get("range_proof").and_then(proof_from_json).ok_or("the reply carries no range proof")?;
    let leaves = leaf_count(cp.mmr_size).map_err(|e| format!("mmr_size: {e}"))?;
    let (from, to) = range.unwrap_or((0, leaves.saturating_sub(1)));
    if (proof.from_index, proof.to_index) != (from, to) {
        return Err(format!("the range proof covers leaves {}..={}, not {from}..={to}", proof.from_index, proof.to_index));
    }
    let (lines, fragment) = complete_lines(copy);
    if fragment {
        return Err("the copy ends in a line with no newline".into());
    }
    let expected = to.checked_sub(from).map(|n| n + 1).ok_or("the range is empty")?;
    if lines.len() as u64 != expected {
        return Err(format!("the copy holds {} lines, but leaves {from}..={to} are {expected}", lines.len()));
    }
    let mut digests = Vec::with_capacity(lines.len());
    let mut redacted = Vec::new();
    for (i, line) in lines.iter().enumerate() {
        let (d, was) = leaf(chain, i, line)?;
        digests.push(d);
        if was {
            redacted.push(i + 1);
        }
    }
    let root = hex_hash(&cp.root).ok_or("the checkpoint's root is not a digest")?;
    if !verify_range(&root, cp.mmr_size, from, to, &digests, &proof) {
        return Err(format!("the copied lines are not leaves {from}..={to} of the checkpointed {file}"));
    }
    Ok(json!({
        "ok": true, "file": file, "lines": lines.len(), "from": from, "to": to, "size_leaves": leaves,
        "log_id": cp.log_id, "cut": cp.timestamp, "key_id": cp.key_id, "leaf_digest": rule,
        // Lines whose leaf digest the copy states (a redacted line) instead of recomputing.
        "redacted_lines": redacted,
    }))
}

fn run(args: &[String]) -> i32 {
    let mut file = None;
    let mut checkpoint = None;
    let mut key = None;
    let mut from = None;
    let mut to = None;
    let mut it = args.iter();
    while let Some(flag) = it.next() {
        let Some(value) = it.next() else {
            eprintln!("{flag} needs a value\n{USAGE}");
            return 2;
        };
        let slot = match flag.as_str() {
            "--file" => &mut file,
            "--checkpoint" => &mut checkpoint,
            "--key" => &mut key,
            "--from" => &mut from,
            "--to" => &mut to,
            _ => {
                eprintln!("unknown argument {flag}\n{USAGE}");
                return 2;
            }
        };
        *slot = Some(value.clone());
    }
    let (Some(file), Some(checkpoint), Some(key)) = (file, checkpoint, key) else {
        eprintln!("{USAGE}");
        return 2;
    };
    let range = match (from.map(|s| s.parse::<u64>()), to.map(|s| s.parse::<u64>())) {
        (None, None) => None,
        (Some(Ok(a)), Some(Ok(b))) => Some((a, b)),
        _ => {
            eprintln!("--from and --to are leaf indices, given together\n{USAGE}");
            return 2;
        }
    };
    let result = std::fs::read(&file)
        .map_err(|e| format!("cannot read {file}: {e}"))
        .and_then(|copy| {
            let text = std::fs::read(&checkpoint).map_err(|e| format!("cannot read {checkpoint}: {e}"))?;
            let reply: Value = serde_json::from_slice(&text).map_err(|e| format!("{checkpoint} is not JSON: {e}"))?;
            check(&copy, &reply, &key, range)
        });
    match result {
        Ok(v) => {
            println!("{v}");
            0
        }
        Err(e) => {
            eprintln!("fail: {e}");
            1
        }
    }
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    std::process::exit(run(&args));
}

#[cfg(test)]
mod tests {
    use super::*;
    use cll::checkpoint::{checkpoint_to_cose, try_sign_checkpoint_digest};
    use cll::mmr::{add_leaf, leaf_hash, peaks, root_from_peaks, MemoryNodeStore, NodeReader};
    use cll::range_proof::range_proof;
    use ed25519_dalek::SigningKey;

    fn signer() -> SigningKey {
        SigningKey::from_bytes(&[7u8; 32])
    }

    fn id(n: u8) -> String {
        hex::encode([n; 32])
    }

    /// The chain a node would hold: one line per record, each stating its capsule_id.
    fn chain(n: u8) -> Vec<u8> {
        (0..n).map(|i| format!("{{\"capsule_id\":\"{}\",\"i\":{i}}}\n", id(i + 1))).collect::<String>().into_bytes()
    }

    /// A reply for the first `upto` leaves of `copy`, the way the plugin's route builds one.
    fn reply_for(file: &str, copy: &[u8], upto: u64, log_id: &str) -> Value {
        let chain = file == CHAIN;
        let mut nodes = MemoryNodeStore::new();
        for (i, line) in complete_lines(copy).0.into_iter().enumerate() {
            let (d, _) = leaf(chain, i, line).unwrap();
            add_leaf(&mut nodes, leaf_hash(&d)).unwrap();
        }
        let size = nodes.size();
        let ps: Vec<Hash> = peaks(size).unwrap().iter().map(|&p| nodes.node(p)).collect();
        let signer = signer();
        let mut cp = CheckpointRecord {
            v: 1,
            kind: "mmr_checkpoint".into(),
            log_id: log_id.into(),
            mmr_size: size,
            root: hex::encode(root_from_peaks(&ps)),
            prev_size: 0,
            prev_root: String::new(),
            key_id: hex::encode(signer.verifying_key().to_bytes()),
            timestamp: "2026-10-31T21:04:00Z".into(),
            signature: String::new(),
            witnesses: vec![],
        };
        cp.signature = try_sign_checkpoint_digest(&cp, &signer).unwrap();
        let cose = checkpoint_to_cose(&cp, &signer, &ps, None, None, None).unwrap();
        let proof = range_proof(&nodes, 0, upto - 1, size).unwrap();
        json!({
            "ledger_checkpoint": 1, "file": file, "leaf_digest": if chain { "capsule_id" } else { "sha256-line" },
            "checkpoint": cp, "checkpoint_cose": hex::encode(cose),
            "range_proof": {"v": proof.v, "kind": proof.kind, "size": proof.size, "from_index": proof.from_index,
                             "to_index": proof.to_index, "witness": proof.witness},
        })
    }

    fn key() -> String {
        hex::encode(signer().verifying_key().to_bytes())
    }

    #[test]
    fn a_whole_chain_checks_and_a_dropped_or_altered_line_does_not() {
        let copy = chain(5);
        let reply = reply_for(CHAIN, &copy, 5, "capsules/abc");
        let ok = check(&copy, &reply, &key(), None).unwrap();
        assert_eq!(ok["lines"], 5);
        let lines: Vec<&[u8]> = complete_lines(&copy).0;
        let dropped = [lines[0], lines[1], lines[3], lines[4]].map(|l| [l, b"\n"].concat()).concat();
        assert!(check(&dropped, &reply, &key(), None).unwrap_err().contains("holds 4 lines"));
        let swapped = String::from_utf8(copy.clone()).unwrap().replace(&id(3), &id(9)).into_bytes();
        assert!(check(&swapped, &reply, &key(), None).unwrap_err().contains("not leaves"));
        assert!(check(&copy[..copy.len() - 1], &reply, &key(), None).unwrap_err().contains("no newline"));
    }

    #[test]
    fn the_key_comes_from_the_caller_not_the_reply() {
        let copy = chain(3);
        let reply = reply_for(CHAIN, &copy, 3, "capsules/abc");
        let other = hex::encode(SigningKey::from_bytes(&[8u8; 32]).verifying_key().to_bytes());
        assert!(check(&copy, &reply, &other, None).unwrap_err().contains("not by the given key"));
    }

    #[test]
    fn a_reply_edited_after_signing_fails() {
        let copy = chain(3);
        let mut reply = reply_for(CHAIN, &copy, 3, "capsules/abc");
        reply["checkpoint"]["timestamp"] = json!("2026-10-31T22:00:00Z");
        assert!(check(&copy, &reply, &key(), None).unwrap_err().contains("does not verify"));
    }

    #[test]
    fn a_chain_reply_for_a_file_log_and_the_reverse_fail() {
        let copy = chain(3);
        let reply = reply_for(CHAIN, &copy, 3, "capsules/abc/ledger/received-capsules.jsonl");
        assert!(check(&copy, &reply, &key(), None).unwrap_err().contains("is not capsules.jsonl's"));
    }

    #[test]
    fn a_redacted_chain_line_checks_by_its_stated_capsule_id() {
        let copy = chain(4);
        let reply = reply_for(CHAIN, &copy, 4, "capsules/abc");
        let mut lines: Vec<String> = String::from_utf8(copy).unwrap().lines().map(String::from).collect();
        lines[2] = format!("{{\"capsule_id\":\"{}\",\"preimage\":\"redacted: a payment preimage never leaves the lab host\",\"x-loop-redacted\":{{\"line_sha256\":\"{}\"}}}}",
                           id(3), id(0));
        let redacted = (lines.join("\n") + "\n").into_bytes();
        let ok = check(&redacted, &reply, &key(), None).unwrap();
        assert_eq!(ok["redacted_lines"], json!([3]));
    }

    #[test]
    fn a_file_with_sha256_line_leaves_checks_and_a_redacted_line_uses_its_marker() {
        let file = "received-provenance.jsonl";
        let original = b"{\"a\":1}\n{\"b\":\"secret\"}\n{\"c\":3}\n".to_vec();
        let sha = hex::encode(Sha256::digest(b"{\"b\":\"secret\"}"));
        let log = "capsules/abc/ledger/received-provenance.jsonl";
        let reply = reply_for(file, &original, 3, log);
        assert_eq!(check(&original, &reply, &key(), None).unwrap()["redacted_lines"], json!([]));
        let kept = format!("{{\"a\":1}}\n{{\"b\":\"redacted: a payment preimage never leaves the lab host\",\"x-loop-redacted\":{{\"line_sha256\":\"{sha}\"}}}}\n{{\"c\":3}}\n");
        assert_eq!(check(kept.as_bytes(), &reply, &key(), None).unwrap()["redacted_lines"], json!([2]));
        let wrong = kept.replace(&sha, &id(1));
        assert!(check(wrong.as_bytes(), &reply, &key(), None).is_err());
        let altered = original.iter().map(|&b| if b == b'3' { b'4' } else { b }).collect::<Vec<_>>();
        assert!(check(&altered, &reply, &key(), None).is_err());
    }

    #[test]
    fn a_marker_on_a_line_that_withheld_no_preimage_is_not_trusted() {
        let file = "received-provenance.jsonl";
        let original = b"{\"a\":1}\n{\"b\":2}\n".to_vec();
        let reply = reply_for(file, &original, 2, "capsules/abc/ledger/received-provenance.jsonl");
        let sha = hex::encode(Sha256::digest(b"{\"b\":2}"));
        let forged = format!("{{\"a\":1}}\n{{\"b\":\"other\",\"x-loop-redacted\":{{\"line_sha256\":\"{sha}\"}}}}\n");
        assert!(check(forged.as_bytes(), &reply, &key(), None).is_err());
    }

    #[test]
    fn an_uncheckpointed_file_is_refused() {
        let copy = chain(2);
        let mut reply = reply_for(CHAIN, &copy, 2, "capsules/abc");
        reply["file"] = json!("rejected-record-pushes.jsonl");
        assert!(check(&copy, &reply, &key(), None).unwrap_err().contains("not a checkpointed ledger file"));
    }

    /// Writes the fixture tests/fixtures/ledger-check uses: LEDGER_FIXTURE_DIR=<dir> cargo test -- fixture
    #[test]
    fn fixture() {
        let Ok(dir) = std::env::var("LEDGER_FIXTURE_DIR") else { return };
        let dir = std::path::Path::new(&dir);
        std::fs::create_dir_all(dir).unwrap();
        let copy = chain(6);
        let mut lines: Vec<String> = String::from_utf8(copy.clone()).unwrap().lines().map(String::from).collect();
        // The node's chain reply covers 5 of the 6 lines the file holds when the copy is read (it grew after).
        let reply = reply_for(CHAIN, &copy[..lines[..5].iter().map(|l| l.len() + 1).sum()], 5, "capsules/abc");
        // A settlement observation's line, its preimage withheld (steps/ledger_proof.py held_a_preimage).
        lines[1] = format!("{{\"capsule_id\":\"{}\",\"x-mesh-settlement-v1\":{{\"payment_hash\":\"cd\",\"preimage\":\"{REDACTED}\"}},\"x-loop-redacted\":{{\"line_sha256\":\"{}\"}}}}",
                           id(2), id(0));
        std::fs::write(dir.join("capsules.jsonl"), (lines.join("\n") + "\n").as_bytes()).unwrap();
        std::fs::write(dir.join("capsules.reply.json"), serde_json::to_vec_pretty(&reply).unwrap()).unwrap();
        std::fs::write(dir.join("key"), key()).unwrap();
        // A sha256-line file (loop#288): a settlement observation's line, kept with its preimage withheld as
        // steps/live.py copy_redacted keeps it, between two plain lines.
        let file = "received-capsules.jsonl";
        let held = r#"{"capsule_id":"c1","x-mesh-settlement-v1":{"payment_hash":"cd","preimage":"ab"}}"#;
        let original = format!("{{\"a\":1}}\n{held}\n{{\"c\":3}}\n");
        let reply = reply_for(file, original.as_bytes(), 3, "capsules/abc/ledger/received-capsules.jsonl");
        let kept = format!("{{\"a\":1}}\n{{\"capsule_id\":\"c1\",\"x-mesh-settlement-v1\":{{\"payment_hash\":\"cd\",\"preimage\":\"{REDACTED}\"}},\"x-loop-redacted\":{{\"line_sha256\":\"{}\"}}}}\n{{\"c\":3}}\n",
                           hex::encode(Sha256::digest(held.as_bytes())));
        std::fs::write(dir.join(file), kept.as_bytes()).unwrap();
        std::fs::write(dir.join("received-capsules.reply.json"), serde_json::to_vec_pretty(&reply).unwrap()).unwrap();
    }
}

import copy
import json
from pathlib import Path

from clim_agent_guard.core import ActionProposal, Decision, EffectStatus, IntegrityGuard, ToolContract
from clim_agent_guard.audit import assess_record, audit_jsonl, main

ROOT = Path(__file__).resolve().parents[1]


def make_guard():
    return IntegrityGuard([ToolContract.from_json(ROOT / 'contracts/file_delete.json')])


def state(root, *, confirmed=True, target='important-notes.txt', version=1, exists=True):
    return {'_version': version, 'user_confirmed': confirmed,
            'sandbox_root': str(root), 'target': {'exists': exists, 'path': target}}


def proposal(path, *, action_id='run-1', version=1, key='operation-1'):
    return ActionProposal('delete_file', {'path': path}, version,
                          action_id=action_id, idempotency_key=key)


def test_assess_allow_matches_precheck_without_mutating(tmp_path):
    guard = make_guard()
    s = state(tmp_path)
    initial = guard.dump_state()
    a = guard.assess(proposal('important-notes.txt'), s)
    assert a.decision == Decision.ALLOW
    assert a.code == 'PRECONDITIONS_SATISFIED'
    assert a.evidence['assessment_only'] is True
    assert guard.dump_state() == initial
    assert guard.precheck(proposal('important-notes.txt'), s).decision == a.decision


def test_assess_policy_failure_and_inputs_unchanged(tmp_path):
    guard = make_guard()
    s = state(tmp_path, confirmed=False)
    before = copy.deepcopy(s)
    d = guard.assess(proposal('other-file.txt'), s)
    assert d.decision == Decision.BLOCK
    assert d.code == 'USER_CONFIRMATION_REQUIRED'
    assert guard.dump_state()['ledger'] == {}
    assert guard.evidence_window() == []
    assert s == before


def test_assess_historical_reserved_retry(tmp_path):
    guard = make_guard()
    s = state(tmp_path)
    original = proposal('important-notes.txt', action_id='original')
    assert guard.precheck(original, s).decision == Decision.ALLOW
    before = guard.dump_state()
    retry = proposal('important-notes.txt', action_id='retry')
    decision = guard.assess(retry, s)
    assert decision.decision == Decision.RECONCILE
    assert decision.code == 'UNKNOWN_PRIOR_EFFECT'
    assert guard.dump_state() == before


def test_assess_committed_operation(tmp_path):
    guard = make_guard()
    s = state(tmp_path)
    p = proposal('important-notes.txt')
    assert guard.precheck(p, s).decision == Decision.ALLOW
    post = state(tmp_path, version=2, exists=False)
    assert guard.verify_effect(p, s, post, EffectStatus.SUCCESS).decision == Decision.ALLOW
    before = guard.dump_state()
    retry = proposal('important-notes.txt', action_id='retry', version=2)
    # Target existence takes precedence over ledger duplicate detection.
    d = guard.assess(retry, post)
    assert d.decision == Decision.BLOCK
    assert d.code == 'TARGET_NOT_PRESENT'
    assert guard.dump_state() == before


def test_audit_jsonl_three_cases_and_missing_state(tmp_path):
    guard = make_guard()
    lines = (ROOT / 'examples/execution_audit_sample.jsonl').read_text().splitlines()
    before = guard.dump_state()
    report = audit_jsonl(lines, guard)
    assert report['counts'] == {'NOT_EVALUATED': 1, 'WOULD_ALLOW': 1, 'WOULD_BLOCK': 2}
    assert report['effect_verification'] == 'NOT_PERFORMED'
    assert all(f['ledger_context'] == 'EMPTY_LEDGER_ASSUMED' for f in report['findings'] if f['status'] != 'NOT_EVALUATED')
    assert all(f['side_effect_status'] == 'NOT_VERIFIED' for f in report['findings'])
    assert guard.dump_state() == before


def test_historical_checkpoint_per_record(tmp_path):
    guard = make_guard()
    s = state(tmp_path)
    p = proposal('important-notes.txt', action_id='first')
    guard.precheck(p, s)
    checkpoint = guard.dump_state()
    # A fresh audit engine reproduces the old reserved attempt from the snapshot.
    fresh = make_guard()
    rec = {
        'proposal': {'tool': 'delete_file', 'args': {'path': 'important-notes.txt'},
                     'observed_state_version': 1, 'action_id': 'second',
                     'idempotency_key': 'operation-1'},
        'authoritative_state': s, 'guard_checkpoint': checkpoint,
    }
    x = assess_record(fresh, rec)
    assert x['code'] == 'UNKNOWN_PRIOR_EFFECT'
    assert x['status'] == 'WOULD_RECONCILE'
    assert x['ledger_context'] == 'HISTORICAL_CHECKPOINT_SUPPLIED'
    assert fresh.dump_state()['ledger'] == {}


def test_unknown_contract_is_not_falsely_reported_safe(tmp_path):
    r = {'proposal': {'tool':'write_file','args':{},'observed_state_version':1,
                      'action_id':'a'}, 'authoritative_state':state(tmp_path)}
    a = assess_record(make_guard(), r)
    assert a['status'] == 'NOT_EVALUATED'
    assert 'No contract' in a['reason']


def test_malformed_and_missing_state_do_not_imply_guard_success(tmp_path):
    report = audit_jsonl(['not-json', '{}', '[]'], make_guard())
    assert report['counts'] == {'NOT_EVALUATED': 3}
    assert all(x['decision'] is None for x in report['findings'])


def test_cli_exit_code_for_unassessed_records(tmp_path):
    output = tmp_path/'audit.json'
    ret = main(['--input', str(ROOT/'examples/execution_audit_sample.jsonl'),
                '--contract', str(ROOT/'contracts/file_delete.json'),
                '--output', str(output)])
    assert ret == 2
    assert json.loads(output.read_text())['record_count'] == 4

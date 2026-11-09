"""Exercise two real accounts, authorization boundaries and persistence.

Creates only uniquely named QA resources and removes them in finally.
Credentials and bearer tokens are never printed or written to disk.
"""
import argparse
import json
import secrets
import time
import requests


def verify(base_url):
    suffix = f"{int(time.time())}{secrets.token_hex(3)}"
    password = secrets.token_urlsafe(24)
    users = [f"weather_qa_{suffix}_{letter}" for letter in ("a", "b")]
    created = []
    results = []
    plan_id = party_id = None
    tokens = {}

    def call(method, path, actor=None, body=None, expected=200, label=None):
        response = requests.request(method, base_url.rstrip('/') + path,
            headers={"Authorization": f"Bearer {tokens[actor]}"} if actor else {}, json=body, timeout=100)
        passed = response.status_code == expected
        if label:
            results.append({"check": label, "passed": passed, "status": response.status_code, "expected": expected})
        if not passed:
            raise AssertionError(f"{label or path}: expected {expected}, received {response.status_code}")
        return response.json()

    try:
        for index, username in enumerate(users):
            created.append(username)
            call('POST', '/api/signup', body={"username": username, "password": password, "name": f"Weather QA {index + 1}"}, label=f"signup_{index + 1}")
            tokens[username] = call('POST', '/api/login', body={"username": username, "password": password}, label=f"login_{index + 1}")["access_token"]
        owner, member = users
        plan = {"name": "QA 산행 계획", "mountain_name": "북한산", "course_index": 0,
                "course_name": "백운대 최단 코스 (탐방지원센터)", "weather_date": "2026-11-10", "selected_date_idx": 1,
                "pace": "normal", "departure_offset_hours": 0, "departure_at": "2026-11-10T00:00:00Z",
                "items": [{"name": "QA 공동 간식", "checked": False}]}
        call('POST', '/api/password/reset', body={"username": member, "name": "Weather QA 2", "new_password": "unauthorized-change"}, expected=422, label='password_change_requires_current_password')
        plan_id = call('POST', '/api/users/me/hiking-plans', owner, plan, label='save_plan')["id"]
        owner_plans = call('GET', '/api/users/me/hiking-plans', owner)
        member_plans = call('GET', '/api/users/me/hiking-plans', member)
        assert any(item['id'] == plan_id for item in owner_plans)
        assert not any(item['id'] == plan_id for item in member_plans)
        results.append({"check": "private_plan_list", "passed": True})
        call('PUT', f'/api/users/me/hiking-plans/{plan_id}', member, plan, expected=404, label='cannot_edit_other_plan')
        call('DELETE', f'/api/users/me/hiking-plans/{plan_id}', member, expected=404, label='cannot_delete_other_plan')
        party_payload = {**plan, "plan_id": plan_id, "name": "QA 공동 준비물 파티"}
        call('POST', '/api/hiking/parties', member, party_payload, expected=404, label='cannot_link_other_plan')
        party_id = call('POST', '/api/hiking/parties', owner, party_payload, label='create_party')["id"]
        party = call('GET', f'/api/hiking/parties/{party_id}', owner)
        item_id = party['items'][0]['id']
        call('GET', f'/api/hiking/parties/{party_id}', expected=401, label='anonymous_party_denied')
        call('GET', f'/api/hiking/parties/{party_id}', member, expected=403, label='outsider_party_denied')
        call('PATCH', f'/api/hiking/parties/{party_id}/items/{item_id}', member, {"is_packed": True}, expected=403, label='outsider_item_write_denied')
        call('PATCH', f'/api/hiking/parties/{party_id}/items/{item_id}', owner, {"assigned_to_username": member}, expected=400, label='outsider_assignment_denied')
        call('POST', f'/api/hiking/parties/{party_id}/invites', owner, {"username": member}, label='invite_member')
        pending = call('GET', f'/api/hiking/parties/{party_id}', owner)
        assert any(item['username'] == member for item in pending.get('pending_invites', []))
        results.append({"check": "owner_can_see_pending_invitation", "passed": True})
        invitations = call('GET', '/api/hiking/parties/invitations', member)
        invitation = next(item for item in invitations if item['party_id'] == party_id)
        call('POST', f'/api/hiking/parties/invitations/{invitation["id"]}/respond', owner, {"decision": "accept"}, expected=404, label='cannot_accept_other_invitation')
        call('POST', f'/api/hiking/parties/invitations/{invitation["id"]}/respond', member, {"decision": "accept"}, label='accept_invitation')
        call('POST', f'/api/hiking/parties/invitations/{invitation["id"]}/respond', member, {"decision": "accept"}, expected=404, label='duplicate_invitation_response_denied')
        party = call('GET', f'/api/hiking/parties/{party_id}', member, label='member_can_read_party')
        assert len(party['members']) == 2
        call('POST', f'/api/hiking/parties/{party_id}/invites', member, {"username": owner}, expected=403, label='member_cannot_invite')
        call('DELETE', f'/api/hiking/parties/{party_id}', member, expected=403, label='member_cannot_delete_party')
        call('DELETE', f'/api/hiking/parties/{party_id}/items/{item_id}', member, expected=403, label='member_cannot_delete_owner_item')
        call('POST', f'/api/hiking/parties/{party_id}/items', member, {"name": "   ", "quantity": 1}, expected=400, label='blank_item_denied')
        call('POST', f'/api/hiking/parties/{party_id}/items', member, {"name": "QA 공동 간식", "quantity": 1}, expected=409, label='duplicate_item_returns_conflict')
        call('PATCH', f'/api/hiking/parties/{party_id}/items/{item_id}', owner, {"quantity": None}, expected=400, label='empty_item_update_denied')
        added = call('POST', f'/api/hiking/parties/{party_id}/items', member, {"name": "QA 생수", "quantity": 2}, label='member_adds_item')
        call('PATCH', f'/api/hiking/parties/{party_id}/items/{added["id"]}', owner,
            {"assigned_to_username": member, "is_packed": True, "quantity": 3}, label='assign_and_pack_item')
        for username in users:
            tokens[username] = call('POST', '/api/login', body={"username": username, "password": password})['access_token']
            restored = call('GET', f'/api/hiking/parties/{party_id}', username)
            restored_item = next(item for item in restored['items'] if item['id'] == added['id'])
            assert restored_item['assigned_to_username'] == member and restored_item['is_packed'] and restored_item['quantity'] == 3
        results.append({"check": "both_accounts_relogin_persistence", "passed": True})
        restored_plans = call('GET', '/api/users/me/hiking-plans', owner)
        assert any(item['id'] == plan_id and item['items'][0]['name'] == 'QA 공동 간식' for item in restored_plans)
        results.append({"check": "saved_plan_relogin_persistence", "passed": True})
    except Exception as exc:
        results.append({"check": "flow_completed", "passed": False, "error_type": type(exc).__name__})
    finally:
        # Account deletion cascades only data created by these two QA accounts.
        for username in reversed(created):
            try:
                response = requests.post(base_url.rstrip('/') + '/api/users/delete', json={"username": username, "password": password}, timeout=40)
                results.append({"check": "cleanup_qa_account", "passed": response.status_code == 200, "status": response.status_code})
            except Exception as exc:
                results.append({"check": "cleanup_qa_account", "passed": False, "error_type": type(exc).__name__})
        print(json.dumps({"passed": all(result['passed'] for result in results), "checks": results}, ensure_ascii=False, indent=2))
    return all(result['passed'] for result in results)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:8000')
    parser.add_argument('--live', action='store_true', help='Explicitly allow temporary QA accounts against a remote deployment')
    args = parser.parse_args()
    if '127.0.0.1' not in args.base_url and 'localhost' not in args.base_url and not args.live:
        parser.error('Use --live to allow remote account creation and cleanup')
    raise SystemExit(0 if verify(args.base_url) else 1)

"""登录鉴权与 RBAC 权限验证（从严用例）。"""

from fastapi.testclient import TestClient

from app.models import Role, User, UserRole
from app.security import hash_password


def _login(client, username="admin", password=None):
    from app.config import get_settings

    pwd = password or get_settings().admin_initial_password
    return client.post("/api/v1/auth/login", json={"username": username, "password": pwd})


def _add_viewer(db):
    viewer = User(username="viewer1", password_hash=hash_password("Viewer@123"), display_name="只读")
    db.add(viewer)
    db.flush()
    role = db.query(Role).filter_by(code="viewer").one()
    db.add(UserRole(user_id=viewer.id, role_id=role.id))
    db.commit()
    return viewer


def _login_viewer(client):
    return client.post("/api/v1/auth/login", json={"username": "viewer1", "password": "Viewer@123"})


def test_health_public(client):
    assert client.get("/health").status_code == 200


def test_api_requires_token():
    """无 token 访问业务接口必须 401。"""
    from app.main import app

    with TestClient(app) as c:
        for path in (
            "/api/v1/tickets",
            "/api/v1/assets",
            "/api/v1/audit",
            "/api/v1/reports/daily",
            "/api/v1/auth/me",
        ):
            assert c.get(path).status_code == 401, path
        assert c.post("/api/v1/auth/logout").status_code == 401


def test_login_success_and_me(client):
    resp = _login(client)
    assert resp.status_code == 200
    body = resp.json()
    assert body["access_token"]
    assert "admin" in body["user"]["roles"]

    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200
    assert me.json()["username"] == "admin"


def test_login_wrong_password_unified_message(client):
    resp = client.post("/api/v1/auth/login", json={"username": "admin", "password": "Wrong@12345"})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "用户名或密码错误"
    # 不存在的用户返回同样的文案（防用户枚举）
    resp2 = client.post("/api/v1/auth/login", json={"username": "ghost", "password": "Wrong@12345"})
    assert resp2.status_code == 401
    assert resp2.json()["detail"] == resp.json()["detail"]


def test_login_lockout_after_5_failures(client):
    for _ in range(5):
        client.post("/api/v1/auth/login", json={"username": "admin", "password": "Bad@12345"})
    resp = _login(client)
    assert resp.status_code == 423  # 账号已锁定


def test_viewer_forbidden_on_operate(client, db):
    _add_viewer(db)
    login = _login_viewer(client)
    assert login.status_code == 200
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 只读用户可读
    assert client.get("/api/v1/tickets", headers=headers).status_code == 200
    # 不可操作
    assert client.post("/api/v1/tickets/1/approve", json={}, headers=headers).status_code == 403
    # 不可访问管理接口
    assert client.get("/api/v1/admin/users", headers=headers).status_code == 403


def test_logout_revokes_token(client):
    login = _login(client)
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    assert client.post("/api/v1/auth/logout", headers=headers).status_code == 200
    # 吊销后再访问 → 401
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 401


def test_change_password_revokes_all_tokens(client):
    login = _login(client)
    old_token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {old_token}"}

    resp = client.post(
        "/api/v1/auth/change-password",
        json={"old_password": "Admin@123456", "new_password": "NewPass@2026"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 401
    # 弱密码被拒绝
    login2 = client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "NewPass@2026"}
    )
    token2 = login2.json()["access_token"]
    weak = client.post(
        "/api/v1/auth/change-password",
        json={"old_password": "NewPass@2026", "new_password": "weak"},
        headers={"Authorization": f"Bearer {token2}"},
    )
    assert weak.status_code == 422


def test_tampered_token_rejected(client):
    from app.security import create_access_token

    login = _login(client)
    token = login.json()["access_token"]
    # 伪造未登记的 jti
    fake, _jti, _exp = create_access_token(1)
    assert client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {fake}"}).status_code == 401
    # 篡改签名
    assert client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}x"}).status_code == 401


def test_admin_user_management(client):
    # 新建 operator 用户
    resp = client.post(
        "/api/v1/admin/users",
        json={"username": "op1", "password": "Oper@1234", "display_name": "操作员", "role_codes": ["operator"]},
    )
    assert resp.status_code == 200
    uid = resp.json()["id"]
    # 用户名重复 → 409
    dup = client.post(
        "/api/v1/admin/users",
        json={"username": "op1", "password": "Oper@1234", "role_codes": ["operator"]},
    )
    assert dup.status_code == 409
    # 弱密码 → 422
    weak = client.post(
        "/api/v1/admin/users",
        json={"username": "op2", "password": "weak", "role_codes": ["viewer"]},
    )
    assert weak.status_code == 422
    # 停用自己 → 409
    me = client.get("/api/v1/auth/me").json()
    self_off = client.patch(f"/api/v1/admin/users/{me['id']}", json={"is_active": False})
    assert self_off.status_code == 409
    # 列表
    users = client.get("/api/v1/admin/users").json()
    assert {u["username"] for u in users["items"]} >= {"admin", "op1"}
    # 解锁接口
    assert client.post(f"/api/v1/admin/users/{uid}/unlock").status_code == 200

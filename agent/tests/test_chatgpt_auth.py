import base64
from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit

from cryptography.hazmat.primitives.asymmetric import rsa
import jwt
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import chatgpt_auth as auth
import providers


def token_set(**changes):
    return {"access_token": "access-secret", "refresh_token": "refresh-secret",
            "id_token": "id-secret", "expires_in": 3600, "token_type": "Bearer",
            "scope": auth.SCOPES, **changes}


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = auth.ChatGPTAuth(Path(self.tmp.name) / "auth/chatgpt.json")

    def seed(self, **changes):
        profile = {"client_id": "oaiapp_test", "issuer": auth.ISSUER, "subject": "u1",
                   "email": "owner@example.test", **auth.token_record(token_set()), **changes}
        with self.store.locked():
            self.store._write({"host_id": "urn:uuid:host", "active_client_id": "oaiapp_test",
                               "profiles": {"oaiapp_test": profile}})
        return profile

    def test_storage_is_private_and_status_never_exposes_tokens(self):
        self.seed()
        self.assertEqual(self.store.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.store.path.parent.stat().st_mode & 0o777, 0o700)
        status = self.store.status()
        self.assertIn("owner@example.test", status)
        self.assertIn("plan usage enabled", status)
        for secret in ("access-secret", "refresh-secret", "id-secret"):
            self.assertNotIn(secret, status)

    def test_missing_login_or_permission_never_uses_api_key(self):
        env = {"OPENAI_API_KEY": "paid-api-key", "VEYRA_CHATGPT_AUTH_FILE": str(self.store.path)}
        with self.assertRaisesRegex(providers.ProviderConfigError, "not signed in"):
            providers.create_provider("chatgpt", env)
        self.seed(scopes=["openid"])
        with self.assertRaisesRegex(providers.ProviderConfigError, "disabled"):
            providers.create_provider("chatgpt", env)
        self.assertFalse(providers.provider_available("chatgpt", env))

    def test_live_token_does_not_refresh(self):
        self.seed()
        with patch.object(auth, "http_json") as network:
            self.assertEqual(self.store.access_token("oaiapp_test"), "access-secret")
            network.assert_not_called()

    def test_custom_credential_path_does_not_change_existing_parent_permissions(self):
        parent = Path(self.tmp.name) / "shared"
        parent.mkdir(mode=0o755)
        store = auth.ChatGPTAuth(parent / "credentials.json")
        store.status()
        self.assertEqual(parent.stat().st_mode & 0o777, 0o755)

    def test_corrupt_profile_is_reported_without_using_its_contents(self):
        with self.store.locked():
            self.store._write({"profiles": {"oaiapp_test": "invalid"}})
        with self.assertRaisesRegex(auth.ChatGPTError, "invalid format"):
            self.store.account()

    def test_refresh_replaces_tokens_and_keeps_registration_and_scopes(self):
        self.seed(expires_at=0)
        replacement = token_set(access_token="new-access", refresh_token="new-refresh")
        replacement.pop("scope")
        with patch.object(auth, "http_json", return_value=replacement) as network:
            self.assertEqual(self.store.access_token(), "new-access")
        request = network.call_args.kwargs["data"]
        self.assertEqual(request["client_id"], "oaiapp_test")
        self.assertNotIn("scope", request)
        saved = self.store._read()["profiles"]["oaiapp_test"]
        self.assertEqual(saved["refresh_token"], "new-refresh")
        self.assertIn(auth.DIRECT_SCOPE, saved["scopes"])
        self.assertEqual(saved["subject"], "u1")

    def test_refresh_is_serialized_between_instances(self):
        self.seed(expires_at=0)
        stores = [auth.ChatGPTAuth(self.store.path) for _ in range(2)]
        results, errors = [], []

        def read(store):
            try:
                results.append(store.access_token())
            except Exception as exc:
                errors.append(exc)

        with patch.object(auth, "http_json", return_value=token_set(access_token="new")) as network:
            threads = [threading.Thread(target=read, args=(store,)) for store in stores]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(5)
            self.assertFalse(any(t.is_alive() for t in threads))
        self.assertEqual(errors, [])
        self.assertEqual(results, ["new", "new"])
        self.assertEqual(network.call_count, 1)

    def test_terminal_refresh_error_clears_tokens_but_keeps_registration(self):
        self.seed(expires_at=0)
        with patch.object(auth, "http_json", side_effect=auth.OAuthError(400, "invalid_grant")):
            with self.assertRaises(auth.ChatGPTError):
                self.store.access_token()
        profile = self.store._read()["profiles"]["oaiapp_test"]
        self.assertEqual(profile["subject"], "u1")
        self.assertFalse(any(key in profile for key in auth.TOKEN_FIELDS))

    def test_temporary_refresh_error_preserves_credentials(self):
        self.seed(expires_at=0)
        before = self.store.path.read_bytes()
        with patch.object(auth, "http_json", side_effect=auth.OAuthError(503, "unavailable")):
            with self.assertRaises(auth.ChatGPTError):
                self.store.access_token()
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_invalid_replacement_does_not_destroy_existing_tokens(self):
        self.seed(expires_at=0)
        before = self.store.path.read_bytes()
        with patch.object(auth, "http_json", return_value=token_set(refresh_token="")):
            with self.assertRaises(auth.ChatGPTError):
                self.store.access_token()
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_switched_account_cannot_continue_old_provider(self):
        self.seed()
        with self.assertRaisesRegex(auth.ChatGPTError, "account changed"):
            self.store.access_token("oaiapp_another")

    def test_logout_revokes_and_preserves_account_mapping(self):
        self.seed()
        metadata = {"revocation_endpoint": auth.ISSUER + "/oauth/revoke"}
        with patch.object(auth, "http_json", side_effect=[metadata, {}]) as network:
            self.assertEqual(self.store.logout(), "Signed out of ChatGPT.")
        self.assertEqual(network.call_args.kwargs["data"]["token"], "refresh-secret")
        self.assertIn("oaiapp_test", self.store._read()["profiles"])
        with self.assertRaisesRegex(auth.ChatGPTError, "not signed in"):
            self.store.access_token()

    def test_offline_logout_clears_tokens_and_reports_unconfirmed_revocation(self):
        self.seed()
        with patch.object(auth, "http_json", side_effect=auth.ChatGPTError("offline")):
            self.assertIn("not confirmed", self.store.logout())
        self.assertNotIn("refresh-secret", self.store.path.read_text())

    def browser(self, responses=None):
        """Return a browser stub that actually exercises the loopback listener."""
        captured = []
        callback_threads = []
        self.addCleanup(lambda: [t.join(5) for t in callback_threads])

        def open_url(url):
            params = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
            captured.append(params)

            def callbacks():
                callback = params["redirect_uri"]
                queries = responses(params) if responses else [
                    {"state": "wrong-state", "code": "ignored"},
                    {"state": params["state"], "code": "test-code", "client_id": "oaiapp_test"},
                ]
                for query in queries:
                    try:
                        requests.get(callback + "?" + urlencode(query), timeout=3)
                    except requests.RequestException:
                        break

            thread = threading.Thread(target=callbacks)
            callback_threads.append(thread)
            thread.start()
            return True

        return open_url, captured

    def test_login_validates_callback_pkce_identity_and_stores_issued_client(self):
        browser, captured = self.browser()
        identity = {"issuer": auth.ISSUER, "subject": "u1", "email": "owner@example.test"}
        with patch.object(auth.webbrowser, "open", side_effect=browser), \
                patch.object(auth, "http_json", return_value=token_set()) as network, \
                patch.object(auth, "validate_identity", return_value=identity) as verify, \
                redirect_stdout(io.StringIO()):
            self.store.login(timeout=5)
        params = captured[0]
        self.assertEqual(params["client_id"], "dynamic_agent_client")
        self.assertEqual(params["agent_name_hint"], "Veyra")
        body = network.call_args.kwargs["data"]
        digest = hashlib.sha256(body["code_verifier"].encode()).digest()
        self.assertEqual(params["code_challenge"], base64.urlsafe_b64encode(digest).decode().rstrip("="))
        self.assertEqual(body["client_id"], "oaiapp_test")
        self.assertEqual(body["redirect_uri"], params["redirect_uri"])
        verify.assert_called_once_with("id-secret", "oaiapp_test", params["nonce"])
        self.assertEqual(self.store.account()["subject"], "u1")

    def test_returning_login_reuses_host_client_and_does_not_print_tokens(self):
        self.seed()
        browser, captured = self.browser(lambda p: [{"state": p["state"], "code": "code"}])
        identity = {"issuer": auth.ISSUER, "subject": "u1", "email": "owner@example.test"}
        output = io.StringIO()
        with patch.object(auth.webbrowser, "open", side_effect=browser), \
                patch.object(auth, "http_json", return_value=token_set()), \
                patch.object(auth, "validate_identity", return_value=identity), redirect_stdout(output):
            self.store.login(timeout=5)
        params = captured[0]
        self.assertEqual(params["client_id"], "oaiapp_test")
        self.assertEqual(params["ext_agent_host_id"], "urn:uuid:host")
        self.assertNotIn("agent_name_hint", params)
        self.assertNotIn("id-secret", output.getvalue())

    def test_failed_identity_does_not_replace_the_active_account(self):
        self.seed()
        browser, _ = self.browser(lambda p: [{"state": p["state"], "code": "code", "client_id": "oaiapp_new"}])
        with patch.object(auth.webbrowser, "open", side_effect=browser), \
                patch.object(auth, "http_json", return_value=token_set()), \
                patch.object(auth, "validate_identity", side_effect=auth.ChatGPTError("bad identity")), \
                redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(auth.ChatGPTError, "bad identity"):
                self.store.login(new_account=True, timeout=5)
        self.assertEqual(self.store.access_token(), "access-secret")
        self.assertEqual(self.store._read()["login_client_id"], "oaiapp_new")

    def test_different_callback_client_or_account_is_rejected(self):
        self.seed()
        for different_client in (True, False):
            browser, _ = self.browser(lambda p: [{"state": p["state"], "code": "code",
                                                   "client_id": "other" if different_client else "oaiapp_test"}])
            identity = {"issuer": auth.ISSUER, "subject": "other", "email": "other@example.test"}
            with patch.object(auth.webbrowser, "open", side_effect=browser), \
                    patch.object(auth, "http_json", return_value=token_set()), \
                    patch.object(auth, "validate_identity", return_value=identity), \
                    redirect_stdout(io.StringIO()):
                with self.assertRaises(auth.ChatGPTError):
                    self.store.login(timeout=5)
            self.assertEqual(self.store.account()["subject"], "u1")

    def test_declined_oauth_does_not_exchange_or_replace_tokens(self):
        self.seed()
        browser, _ = self.browser(lambda p: [{"state": p["state"], "error": "access_denied"}])
        with patch.object(auth.webbrowser, "open", side_effect=browser), \
                patch.object(auth, "http_json") as network, redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(auth.ChatGPTError, "declined"):
                self.store.login(timeout=5)
        network.assert_not_called()
        self.assertEqual(self.store.access_token(), "access-secret")

    def test_unanswered_login_times_out_without_creating_credentials(self):
        with patch.object(auth.webbrowser, "open", return_value=True), redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(auth.ChatGPTError, "timed out"):
                self.store.login(timeout=0)
        self.assertEqual(self.store._read()["profiles"], {})


class IdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def test_signed_id_token_checks_signature_issuer_audience_expiry_and_nonce(self):
        claims = {"iss": auth.ISSUER, "aud": "oaiapp_test", "sub": "user",
                  "exp": int(time.time()) + 60, "nonce": "nonce"}
        for change in ({}, {"iss": "https://attacker.test"}, {"aud": "another-client"},
                       {"exp": 1}, {"nonce": "wrong"}, {"sub": ""}):
            token = jwt.encode({**claims, **change}, self.key, algorithm="RS256")
            with patch.object(auth, "http_json", return_value={"issuer": auth.ISSUER, "jwks_uri": auth.ISSUER + "/jwks"}), \
                    patch.object(jwt.PyJWKClient, "get_signing_key_from_jwt", return_value=type("Key", (), {"key": self.key.public_key()})()):
                if change:
                    with self.assertRaises(auth.ChatGPTError):
                        auth.validate_identity(token, "oaiapp_test", "nonce")
                else:
                    self.assertEqual(auth.validate_identity(token, "oaiapp_test", "nonce")["subject"], "user")

    def test_forged_signature_is_rejected(self):
        wrong_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        token = jwt.encode({"iss": auth.ISSUER, "aud": "client", "sub": "user",
                            "nonce": "nonce", "exp": int(time.time()) + 60}, wrong_key, algorithm="RS256")
        with patch.object(auth, "http_json", return_value={"issuer": auth.ISSUER, "jwks_uri": auth.ISSUER + "/jwks"}), \
                patch.object(jwt.PyJWKClient, "get_signing_key_from_jwt", return_value=type("Key", (), {"key": self.key.public_key()})()):
            with self.assertRaisesRegex(auth.ChatGPTError, "verification failed"):
                auth.validate_identity(token, "client", "nonce")


if __name__ == "__main__":
    unittest.main()

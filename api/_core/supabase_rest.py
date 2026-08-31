"""Minimal Supabase REST client (stdlib only): auth, PostgREST, storage."""
import json
import urllib.error
import urllib.request


class SupabaseError(Exception):
    pass


class SB:
    def __init__(self, url: str, service_key: str, timeout: int = 30):
        self.url = url.rstrip("/")
        self.key = service_key
        self.timeout = timeout

    def _req(self, method, path, body=None, headers=None, raw=None, content_type=None, expect_json=True):
        hdrs = {"apikey": self.key, "Authorization": f"Bearer {self.key}"}
        if headers:
            hdrs.update(headers)
        data = None
        if raw is not None:
            data = raw
            hdrs["Content-Type"] = content_type or "application/octet-stream"
        elif body is not None:
            data = json.dumps(body).encode()
            hdrs["Content-Type"] = "application/json"
        req = urllib.request.Request(f"{self.url}{path}", data=data, method=method, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = resp.read()
                if not expect_json:
                    return payload
                return json.loads(payload) if payload else None
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:400]
            raise SupabaseError(f"{method} {path} -> {e.code}: {detail}") from e

    # ---- auth ----
    def get_user(self, access_token: str):
        """Returns user dict or None."""
        try:
            return self._req("GET", "/auth/v1/user",
                             headers={"Authorization": f"Bearer {access_token}"})
        except SupabaseError:
            return None

    # ---- PostgREST ----
    def select(self, table: str, query: str, token: str | None = None):
        hdrs = {}
        if token:
            hdrs["Authorization"] = f"Bearer {token}"
        rows = self._req("GET", f"/rest/v1/{table}?{query}", headers=hdrs)
        return rows or []

    def insert(self, table: str, rows: list, upsert: bool = False, returning: str = "representation"):
        hdrs = {"Prefer": f"return={returning}" + (",resolution=merge-duplicates" if upsert else "")}
        return self._req("POST", f"/rest/v1/{table}", body=rows, headers=hdrs)

    def rpc(self, fn: str, args: dict):
        return self._req("POST", f"/rest/v1/rpc/{fn}", body=args)

    # ---- storage ----
    def storage_upload(self, bucket: str, path: str, data: bytes, content_type: str):
        return self._req("POST", f"/storage/v1/object/{bucket}/{path}",
                         raw=data, content_type=content_type, expect_json=True)

    def create_signed_url(self, bucket: str, path: str, expires_in: int = 600):
        r = self._req("POST", f"/storage/v1/object/sign/{bucket}/{path}",
                      body={"expiresIn": expires_in})
        return r.get("signedURL") or r.get("signedUrl")

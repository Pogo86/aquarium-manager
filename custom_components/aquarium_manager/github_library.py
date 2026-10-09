"""GitHub-backed fish-library writer."""
from __future__ import annotations

import base64
from copy import deepcopy
from datetime import date
import json
from typing import Any
from urllib.parse import quote, urlsplit

from aiohttp import ClientError, ClientTimeout
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .species import SpeciesLibraryError, validate_library_document

GITHUB_API = "https://api.github.com"
GITHUB_API_VERSION = "2026-03-10"

class GitHubLibraryError(RuntimeError):
    pass


def github_target_from_raw_url(url: str) -> dict[str, str] | None:
    """Parse a raw GitHub content URL into owner/repo/branch/path.

    GitHub currently emits both of these valid raw URL shapes:

      raw.githubusercontent.com/OWNER/REPO/BRANCH/path/to/file.json
      raw.githubusercontent.com/OWNER/REPO/refs/heads/BRANCH/path/to/file.json

    The second form must not treat ``refs`` as the branch name.
    """
    parts = urlsplit((url or "").strip())
    if parts.scheme not in {"http", "https"} or parts.netloc.lower() != "raw.githubusercontent.com":
        return None

    bits = [x for x in parts.path.split("/") if x]
    if len(bits) < 4:
        return None

    owner, repo = bits[0], bits[1]

    if len(bits) >= 6 and bits[2] == "refs" and bits[3] == "heads":
        branch = bits[4]
        path = "/".join(bits[5:])
    else:
        branch = bits[2]
        path = "/".join(bits[3:])

    if not all((owner, repo, branch, path)):
        return None
    return {"owner": owner, "repo": repo, "branch": branch, "path": path}


def _norm(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def upsert_species_document(document: dict[str, Any], record: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """Return an updated document and Add/Update action."""
    updated = deepcopy(document)
    species = updated.get("species")
    if not isinstance(species, list):
        raise GitHubLibraryError("Remote fish_library.json has no species list.")

    incoming_id = str(record.get("id") or "").strip()
    incoming_sci = _norm(record.get("scientific_name"))
    match_index = None
    for i, item in enumerate(species):
        if not isinstance(item, dict):
            continue
        if incoming_id and str(item.get("id") or "") == incoming_id:
            match_index = i
            break
        if incoming_sci and _norm(item.get("scientific_name")) == incoming_sci:
            match_index = i
            break

    clean = deepcopy(record)
    if match_index is None:
        species.append(clean)
        action = "Add"
    else:
        # Replace the source-managed record; this keeps the JSON consistent with
        # what was reviewed in the Species Library tab.
        species[match_index] = clean
        action = "Update"

    species.sort(key=lambda x: str(x.get("common_name") or x.get("scientific_name") or "").casefold())
    updated["updated"] = date.today().isoformat()
    try:
        validate_library_document(updated)
    except SpeciesLibraryError as err:
        raise GitHubLibraryError(f"Updated library is invalid: {err}") from err
    return updated, action


class GitHubLibraryWriter:
    def __init__(self, hass: HomeAssistant, raw_url: str, token: str) -> None:
        self.hass = hass
        self.target = github_target_from_raw_url(raw_url)
        self.token = (token or "").strip()

    @property
    def ready(self) -> bool:
        return bool(self.target and self.token)

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
            "User-Agent": "AquariumManager/0.8.2",
        }

    def _api_url(self) -> str:
        if not self.target:
            raise GitHubLibraryError("The fish-library URL is not a raw GitHub URL.")
        t = self.target
        return f"{GITHUB_API}/repos/{quote(t['owner'])}/{quote(t['repo'])}/contents/{quote(t['path'], safe='/')}"

    async def async_upsert(self, record: dict[str, Any]) -> dict[str, Any]:
        if not self.ready:
            raise GitHubLibraryError(
                "GitHub write access is not configured. Add a fine-grained token in Aquarium Manager → Configure."
            )
        session = async_get_clientsession(self.hass)
        timeout = ClientTimeout(total=30)
        t = self.target or {}
        try:
            async with session.get(
                self._api_url(), params={"ref": t["branch"]}, headers=self._headers(), timeout=timeout
            ) as response:
                if response.status == 401:
                    raise GitHubLibraryError("GitHub authentication failed. Check the configured token.")
                if response.status == 403:
                    raise GitHubLibraryError("GitHub denied access. The token needs Contents: read/write permission.")
                if response.status != 200:
                    raise GitHubLibraryError(f"GitHub returned HTTP {response.status} while reading the library.")
                payload = await response.json()

            sha = str(payload.get("sha") or "")
            encoded = str(payload.get("content") or "").replace("\n", "")
            if not sha or str(payload.get("encoding")) != "base64" or not encoded:
                raise GitHubLibraryError("GitHub returned an unexpected file response.")
            try:
                remote = json.loads(base64.b64decode(encoded).decode("utf-8"))
            except Exception as err:
                raise GitHubLibraryError("The GitHub fish_library.json is not valid UTF-8 JSON.") from err

            # Validate the remote before modifying it.
            try:
                validate_library_document(remote)
            except SpeciesLibraryError as err:
                raise GitHubLibraryError(f"Current GitHub library is invalid: {err}") from err

            updated, action = upsert_species_document(remote, record)
            common = str(record.get("common_name") or record.get("scientific_name") or "species").strip()
            commit_message = f"{action} {common}"
            new_content = base64.b64encode(
                (json.dumps(updated, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
            ).decode("ascii")

            async with session.put(
                self._api_url(),
                headers=self._headers(),
                timeout=timeout,
                json={
                    "message": commit_message,
                    "content": new_content,
                    "sha": sha,
                    "branch": t["branch"],
                },
            ) as response:
                if response.status in {409, 422}:
                    raise GitHubLibraryError(
                        "GitHub rejected the update because the remote file changed. Refresh and try again."
                    )
                if response.status == 401:
                    raise GitHubLibraryError("GitHub authentication failed while writing the library.")
                if response.status == 403:
                    raise GitHubLibraryError("GitHub denied the write. The token needs Contents: read/write permission.")
                if response.status not in {200, 201}:
                    raise GitHubLibraryError(f"GitHub returned HTTP {response.status} while writing the library.")
                result = await response.json()

            commit = result.get("commit") if isinstance(result, dict) else {}
            return {
                "document": updated,
                "action": action.lower(),
                "commit_message": commit_message,
                "commit_sha": str((commit or {}).get("sha") or ""),
            }
        except ClientError as err:
            raise GitHubLibraryError(f"Could not contact GitHub: {err}") from err

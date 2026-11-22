"""Is a policy file one only an administrator could have written?

A location a person (or anything running as them) can write is never a policy source: a policy there could
point Row-Bot at someone else's gateway as easily as it could restrict it. Each check returns why a file is
refused, or None.

- POSIX: the file and every directory above it, both as named and after resolving links, are owned by root
  and writable by nobody else.
- Windows: the file and its folders below ``boundary`` (``%ProgramData%`` for the standard location, the
  drive for ``ROW_BOT_POLICY_FILE``) are not links, are owned by SYSTEM, Administrators or TrustedInstaller,
  and grant no one else the right to change the file or replace what the folders hold. Adding a new file to a
  folder is allowed (``%ProgramData%`` grants that to Users by inheritance); replacing or deleting one is not.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Callable, Sequence
from pathlib import Path

# Owners and trustees that are administrators: SYSTEM, Administrators, TrustedInstaller.
_ADMIN_SIDS = frozenset({
    "S-1-5-18",
    "S-1-5-32-544",
    "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464",
})
# CREATOR OWNER and OWNER RIGHTS stand for the owner, which is checked to be an administrator.
_TRUSTEE_SIDS = _ADMIN_SIDS | {"S-1-3-0", "S-1-3-4"}
_ACCESS_ALLOWED = 0
_ACCESS_DENIED_TYPES = frozenset({1, 6, 10, 12})  # denied, denied object, denied callback (object)
_INHERIT_ONLY = 0x08
_DELETE, _WRITE_DAC, _WRITE_OWNER = 0x10000, 0x40000, 0x80000
_GENERIC_ALL, _GENERIC_WRITE = 0x10000000, 0x40000000
_FILE_WRITE = 0x2 | 0x4 | _DELETE | _WRITE_DAC | _WRITE_OWNER | _GENERIC_ALL | _GENERIC_WRITE
# On a folder: delete child, plus deleting, re-permissioning or taking over the folder itself.
_FOLDER_REPLACE = 0x40 | _DELETE | _WRITE_DAC | _WRITE_OWNER | _GENERIC_ALL
_REPARSE_POINT = 0x400

Ace = tuple[int, int, int, str]  # (type, flags, access mask, trustee SID)


def trust_problem(path: Path, boundary: Path | None = None) -> str | None:
    """Why *path* can't be trusted as written by an administrator, or None when it can."""
    try:
        if os.name == "nt":
            return _windows_problem(path, boundary)
        return posix_problem(path)
    except OSError as exc:
        return f"{path}: can't check who may change it ({exc.strerror or exc.__class__.__name__})"


def posix_problem(path: Path, lstat: Callable[[Path], os.stat_result] = os.lstat,
                  realpath: Callable[[Path], str] = os.path.realpath) -> str | None:
    """The POSIX check; *lstat* and *realpath* are seams for tests."""
    resolved = Path(realpath(path))
    if not stat.S_ISREG(lstat(resolved).st_mode):
        return f"{path}: not a regular file"
    for named in (Path(os.path.abspath(path)), resolved):
        for entry in (named, *named.parents):
            info = lstat(entry)
            if stat.S_ISLNK(info.st_mode):
                continue  # a link can only be replaced through its folder, which is checked too
            if info.st_uid != 0:
                return f"{entry}: owned by uid {info.st_uid}, not root"
            if info.st_mode & 0o022:
                return f"{entry}: users other than root can change it"
    return None


def windows_acl_problem(owner_sid: str, aces: Sequence[Ace] | None, *, folder: bool) -> str | None:
    """Why an owner and access list let someone other than an administrator change a file (or replace what a
    folder holds), or None. *aces* is None for a missing access list, which lets everyone do anything."""
    if owner_sid not in _ADMIN_SIDS:
        return f"owned by {owner_sid}, not an administrator"
    if aces is None:
        return "has no access list, so everyone may change it"
    risky = _FOLDER_REPLACE if folder else _FILE_WRITE
    for ace_type, flags, mask, sid in aces:
        if flags & _INHERIT_ONLY or ace_type in _ACCESS_DENIED_TYPES:
            continue
        if ace_type != _ACCESS_ALLOWED:
            return f"has an access entry Row-Bot can't check (type {ace_type})"
        if mask & risky and sid not in _TRUSTEE_SIDS:
            return f"{sid} may change it"
    return None


def _windows_entries(path: Path, boundary: Path | None) -> list[tuple[Path, bool]]:
    entries = [(path, False)]
    stop = os.path.normcase(str(boundary)) if boundary is not None else None
    for parent in path.parents:
        text = os.path.normcase(str(parent))
        if parent == Path(parent.anchor):
            break
        if stop is not None and (text == stop or not text.startswith(stop.rstrip("\\") + "\\")):
            break
        entries.append((parent, True))
    return entries


def _windows_problem(path: Path, boundary: Path | None) -> str | None:
    if not path.is_absolute():
        return f"{path}: not an absolute path"
    for entry, folder in _windows_entries(path, boundary):
        info = os.lstat(entry)
        if getattr(info, "st_file_attributes", 0) & _REPARSE_POINT:
            return f"{entry}: a link, not a {'folder' if folder else 'file'}"
        if not folder and not stat.S_ISREG(info.st_mode):
            return f"{entry}: not a regular file"
        owner, aces = _windows_security(str(entry))
        problem = windows_acl_problem(owner, aces, folder=folder)
        if problem:
            return f"{entry}: {problem}"
    return None


def _windows_security(path: str) -> tuple[str, list[Ace] | None]:
    """The owner SID and the access list of *path*, read with GetNamedSecurityInfoW."""
    import ctypes
    from ctypes import wintypes

    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    get_info = advapi.GetNamedSecurityInfoW
    get_info.argtypes = [wintypes.LPCWSTR, ctypes.c_int, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p),
                         ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
                         ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p)]
    get_info.restype = wintypes.DWORD
    get_ace = advapi.GetAce
    get_ace.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)]
    get_ace.restype = wintypes.BOOL
    to_string = advapi.ConvertSidToStringSidW
    to_string.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    to_string.restype = wintypes.BOOL
    local_free = kernel.LocalFree
    local_free.argtypes = [ctypes.c_void_p]
    local_free.restype = ctypes.c_void_p

    class AclHeader(ctypes.Structure):
        _fields_ = [("revision", ctypes.c_ubyte), ("sbz1", ctypes.c_ubyte), ("size", ctypes.c_ushort),
                    ("count", ctypes.c_ushort), ("sbz2", ctypes.c_ushort)]

    class AceHeader(ctypes.Structure):
        _fields_ = [("type", ctypes.c_ubyte), ("flags", ctypes.c_ubyte), ("size", ctypes.c_ushort)]

    def sid_text(sid: int) -> str:
        text = wintypes.LPWSTR()
        if not to_string(sid, ctypes.byref(text)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return text.value or ""
        finally:
            local_free(ctypes.cast(text, ctypes.c_void_p))

    owner, dacl, descriptor = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
    # SE_FILE_OBJECT; OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION
    status = get_info(path, 1, 0x1 | 0x4, ctypes.byref(owner), None, ctypes.byref(dacl), None,
                      ctypes.byref(descriptor))
    if status:
        raise ctypes.WinError(status)
    try:
        owner_sid = sid_text(owner.value)
        if not dacl.value:
            return owner_sid, None
        aces: list[Ace] = []
        for index in range(AclHeader.from_address(dacl.value).count):
            ace = ctypes.c_void_p()
            if not get_ace(dacl, index, ctypes.byref(ace)):
                raise ctypes.WinError(ctypes.get_last_error())
            header = AceHeader.from_address(ace.value)
            if header.type in (_ACCESS_ALLOWED, 1):  # ACCESS_ALLOWED_ACE / ACCESS_DENIED_ACE: mask, then SID
                mask = ctypes.c_uint32.from_address(ace.value + 4).value
                aces.append((header.type, header.flags, mask, sid_text(ace.value + 8)))
            else:
                aces.append((header.type, header.flags, 0, ""))
        return owner_sid, aces
    finally:
        local_free(descriptor)

/* Read-only Win64 observation adapter for the fixed disposable S3 lab.
 * No sockets, SQL, process creation/termination, memory reads or cached facts.
 * The caller releases the GIL for this complete query. */
#define WIN32_LEAN_AND_MEAN
#define _WIN32_WINNT 0x0A00
#include <windows.h>
#include <bcrypt.h>

BOOL WINAPI DllMain(HINSTANCE instance, DWORD reason, LPVOID reserved) {
    (void)instance; (void)reason; (void)reserved;
    return TRUE;
}

static BOOL observe(HANDLE process, DWORD pid, ULONGLONG *created,
                    LPWSTR image, DWORD capacity, DWORD *length, DWORD *error) {
    FILETIME birth, exit, kernel, user;
    DWORD n = capacity;
    *created = 0; *length = 0; *error = 0; image[0] = L'\0';
    if (GetProcessId(process) != pid) { *error = ERROR_INVALID_DATA; return FALSE; }
    if (!QueryFullProcessImageNameW(process, 0, image, &n) ||
        !GetProcessTimes(process, &birth, &exit, &kernel, &user)) {
        *error = GetLastError(); return FALSE;
    }
    if (!n || n >= capacity || image[n] != L'\0' ||
        exit.dwHighDateTime || exit.dwLowDateTime ||
        (!birth.dwHighDateTime && !birth.dwLowDateTime)) {
        *error = ERROR_INVALID_DATA; return FALSE;
    }
    *created = ((ULONGLONG)birth.dwHighDateTime << 32) | birth.dwLowDateTime;
    *length = n;
    return TRUE;
}

__declspec(dllexport) BOOL WINAPI pr03b_observe_handle(
    HANDLE process, DWORD pid, ULONGLONG *created, LPWSTR image,
    DWORD capacity, DWORD *length, DWORD *error) {
    if (!process || !pid || !created || !image || capacity < 2 ||
        capacity > 32768 || !length || !error) {
        if (error) *error = ERROR_INVALID_PARAMETER;
        return FALSE;
    }
    return observe(process, pid, created, image, capacity, length, error);
}

__declspec(dllexport) BOOL WINAPI pr03b_observe_pid(
    DWORD pid, ULONGLONG *created, LPWSTR image, DWORD capacity,
    DWORD *length, DWORD *error) {
    HANDLE process;
    BOOL ok;
    if (!pid || !created || !image || capacity < 2 || capacity > 32768 ||
        !length || !error) {
        if (error) *error = ERROR_INVALID_PARAMETER;
        return FALSE;
    }
    process = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, pid);
    if (!process) { *error = GetLastError(); return FALSE; }
    ok = observe(process, pid, created, image, capacity, length, error);
    if (!CloseHandle(process)) { *error = GetLastError(); return FALSE; }
    return ok;
}

/* Fixed-width, borrowed readonly file observations. Expected IDs/digests are
 * immutable parameters, never previously successful observations. Every call
 * re-queries each handle, including both sides of every complete source hash.
 * Names here are exclusively the pinned ASCII guest paths. No fallback. */
typedef struct {
    HANDLE handle;
    DWORD volume, index_hi, index_lo, attributes, size_hi, size_lo, links;
    DWORD written_hi, written_lo, directory;
    LPCWSTR path;
    const BYTE *data;
    DWORD length;
    BYTE digest[32];
    DWORD path_after;
} PR03B_FILE;
_Static_assert(sizeof(PR03B_FILE) == 104, "file observation ABI");

static BOOL live(ULONGLONG deadline, DWORD *error) {
    if (deadline && GetTickCount64() >= deadline) {
        *error = ERROR_TIMEOUT; return FALSE;
    }
    return TRUE;
}
static WCHAR folded(WCHAR value) {
    return value >= L'A' && value <= L'Z' ? value + (L'a' - L'A') : value;
}
static BOOL named(const PR03B_FILE *item, LPWSTR scratch, DWORD capacity,
                  DWORD *error) {
    DWORD n, i;
    if (item->path) {
        n = GetFinalPathNameByHandleW(item->handle, scratch, capacity, 8);
        if (!n || n >= capacity) { *error = ERROR_INVALID_DATA; return FALSE; }
        for (i = 0; i < n; ++i)
            if (!item->path[i] || folded(scratch[i]) != folded(item->path[i])) {
                *error = ERROR_INVALID_DATA; return FALSE;
            }
        if (item->path[n]) { *error = ERROR_INVALID_DATA; return FALSE; }
    }
    return TRUE;
}
static BOOL metadata(const PR03B_FILE *item, LPWSTR scratch, DWORD capacity,
                     DWORD *error) {
    BY_HANDLE_FILE_INFORMATION actual;
    if (!item->path_after && !named(item, scratch, capacity, error)) return FALSE;
    if (!GetFileInformationByHandle(item->handle, &actual)) {
        *error = GetLastError(); return FALSE;
    }
    if ((actual.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) ||
        !!(actual.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) != !!item->directory ||
        actual.dwVolumeSerialNumber != item->volume ||
        actual.nFileIndexHigh != item->index_hi || actual.nFileIndexLow != item->index_lo) {
        *error = ERROR_INVALID_DATA; return FALSE;
    }
    if (!item->directory &&
        (actual.dwFileAttributes != item->attributes ||
         actual.nFileSizeHigh != item->size_hi || actual.nFileSizeLow != item->size_lo ||
         actual.nNumberOfLinks != 1 || actual.nNumberOfLinks != item->links ||
         actual.ftLastWriteTime.dwHighDateTime != item->written_hi ||
         actual.ftLastWriteTime.dwLowDateTime != item->written_lo ||
         actual.nFileSizeHigh || actual.nFileSizeLow > 256u * 1024u * 1024u)) {
        *error = ERROR_INVALID_DATA; return FALSE;
    }
    return !item->path_after || named(item, scratch, capacity, error);
}
__declspec(dllexport) BOOL WINAPI pr03b_match_files(
    const PR03B_FILE *items, DWORD count, LPWSTR scratch, DWORD capacity,
    ULONGLONG deadline, DWORD *error) {
    DWORD i;
    if (!items || !count || count > 256 || !scratch || capacity != 32768 || !error) {
        if (error) *error = ERROR_INVALID_PARAMETER;
        return FALSE;
    }
    *error = 0;
    for (i = 0; i < count; ++i)
        if (!live(deadline, error) || !metadata(items + i, scratch, capacity, error)) return FALSE;
    return live(deadline, error);
}
__declspec(dllexport) BOOL WINAPI pr03b_verify_sources(
    const PR03B_FILE *items, DWORD count, LPWSTR scratch, DWORD capacity,
    ULONGLONG deadline, DWORD *error) {
    BCRYPT_ALG_HANDLE algorithm = NULL;
    DWORD i, j;
    BYTE digest[32];
    BOOL okay = FALSE;
    if (!items || !count || count > 256 || !scratch || capacity != 32768 || !error) {
        if (error) *error = ERROR_INVALID_PARAMETER;
        return FALSE;
    }
    *error = 0;
    if (BCryptOpenAlgorithmProvider(&algorithm, BCRYPT_SHA256_ALGORITHM, NULL, 0) < 0) {
        *error = ERROR_INVALID_FUNCTION; return FALSE;
    }
    for (i = 0; i < count; ++i) {
        const PR03B_FILE *item = items + i;
        if (item->directory || item->path || !item->data || item->size_hi ||
            item->length != item->size_lo || item->length > 256u * 1024u * 1024u) {
            *error = ERROR_INVALID_PARAMETER; goto finish;
        }
        if (!live(deadline, error) || !metadata(item, scratch, capacity, error)) goto finish;
        if (BCryptHash(algorithm, NULL, 0, (PUCHAR)item->data, item->length, digest, 32) < 0) {
            *error = ERROR_INVALID_FUNCTION; goto finish;
        }
        for (j = 0; j < 32; ++j) if (digest[j] != item->digest[j]) {
            *error = ERROR_INVALID_DATA; goto finish;
        }
        if (!live(deadline, error) || !metadata(item, scratch, capacity, error)) goto finish;
    }
    okay = live(deadline, error);
finish:
    if (BCryptCloseAlgorithmProvider(algorithm, 0) < 0) {
        *error = ERROR_INVALID_FUNCTION; return FALSE;
    }
    return okay;
}

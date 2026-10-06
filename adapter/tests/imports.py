import argparse
import struct
from pathlib import Path


WINDOWS7_IMPORTS = {
    "kernel32.dll": set("""
        AreFileApisANSI CloseHandle CompareStringW CreateFileW DecodePointer
        DeleteCriticalSection EncodePointer EnterCriticalSection EnumSystemLocalesW
        ExitProcess FindClose FindFirstFileW FindFirstFileExW FindNextFileW
        FlsAlloc FlsFree FlsGetValue FlsSetValue FlushFileBuffers FormatMessageA
        FreeEnvironmentStringsW FreeLibrary GetACP GetCPInfo GetCommandLineA
        GetCommandLineW GetConsoleMode GetConsoleOutputCP GetCurrentProcess
        GetCurrentProcessId GetCurrentThreadId GetEnvironmentStringsW
        GetFileAttributesExW GetFileAttributesW GetFileInformationByHandleEx
        GetFileSizeEx GetFileType GetFullPathNameW GetLastError GetLocalTime
        GetLocaleInfoEx GetLocaleInfoW GetModuleFileNameW GetModuleHandleExW
        GetModuleHandleW GetOEMCP GetPrivateProfileStringW GetProcAddress
        GetProcessHeap GetStartupInfoW GetStdHandle GetStringTypeW
        GetSystemTimeAsFileTime GetTickCount64 GetUserDefaultLCID HeapAlloc
        HeapFree HeapReAlloc HeapSize InitializeCriticalSectionEx InitializeSListHead
        IsDebuggerPresent IsProcessorFeaturePresent IsValidCodePage IsValidLocale
        LCMapStringEx LCMapStringW LeaveCriticalSection LoadLibraryExW LocalFree
        MultiByteToWideChar OpenProcess QueryFullProcessImageNameW
        QueryPerformanceCounter RaiseException ReadConsoleW ReadFile RtlCaptureContext
        RtlLookupFunctionEntry RtlPcToFileHeader RtlUnwind RtlUnwindEx RtlVirtualUnwind
        SetConsoleOutputCP SetEndOfFile SetEnvironmentVariableW SetFilePointerEx
        SetLastError SetStdHandle SetUnhandledExceptionFilter Sleep TerminateProcess
        UnhandledExceptionFilter VirtualAllocEx VirtualFreeEx VirtualProtect
        WaitForSingleObject WideCharToMultiByte WriteConsoleW WriteFile WriteProcessMemory
    """.split()),
    "user32.dll": {"EnumWindows", "GetClassNameW", "GetWindowThreadProcessId", "SendMessageTimeoutW"},
    "bcrypt.dll": {
        "BCryptOpenAlgorithmProvider", "BCryptHashData", "BCryptFinishHash",
        "BCryptDestroyHash", "BCryptCloseAlgorithmProvider", "BCryptCreateHash",
    },
}


def verify(path):
    data = Path(path).read_bytes()
    pe_offset = struct.unpack_from("<I", data, 0x3C)[0]
    if data[:2] != b"MZ" or data[pe_offset:pe_offset + 4] != b"PE\0\0":
        raise ValueError("Not a PE executable")
    machine, section_count = struct.unpack_from("<HH", data, pe_offset + 4)
    optional_size = struct.unpack_from("<H", data, pe_offset + 20)[0]
    optional = pe_offset + 24
    if machine != 0x8664 or struct.unpack_from("<H", data, optional)[0] != 0x20B:
        raise ValueError("Requires x64 PE32+")
    for name, offset in (("OS", 40), ("subsystem", 48)):
        version = struct.unpack_from("<HH", data, optional + offset)
        if version > (6, 1):
            raise ValueError(f"{name} version {version} requires Windows newer than 7")
    sections = []
    for index in range(section_count):
        start = optional + optional_size + index * 40
        virtual_size, virtual_address, raw_size, raw_offset = struct.unpack_from("<IIII", data, start + 8)
        sections.append((virtual_address, max(virtual_size, raw_size), raw_offset))

    def address(rva):
        for virtual_address, size, raw_offset in sections:
            if virtual_address <= rva < virtual_address + size:
                return rva - virtual_address + raw_offset
        raise ValueError(f"Unmapped RVA {rva:x}")

    def string(rva):
        start = address(rva)
        return data[start:data.index(b"\0", start)].decode("ascii")

    delay_rva = struct.unpack_from("<I", data, optional + 112 + 13 * 8)[0]
    if delay_rva:
        raise ValueError("Delay imports require a separate compatibility audit")
    imports_rva = struct.unpack_from("<I", data, optional + 112 + 8)[0]
    descriptor = address(imports_rva)
    failures = []
    count = 0
    while True:
        lookup, timestamp, forward, name_rva, first_thunk = struct.unpack_from("<IIIII", data, descriptor)
        if not any((lookup, timestamp, forward, name_rva, first_thunk)):
            break
        library = string(name_rva).lower()
        thunk = address(lookup or first_thunk)
        while True:
            entry = struct.unpack_from("<Q", data, thunk)[0]
            if not entry:
                break
            function = f"ordinal {entry & 0xffff}" if entry >> 63 else string(entry + 2)
            if function not in WINDOWS7_IMPORTS.get(library, set()):
                failures.append(f"{library}!{function}")
            count += 1
            thunk += 8
        descriptor += 20
    if failures:
        raise ValueError("Imports outside the reviewed Windows 7 API set: " + ", ".join(failures))
    print(f"PASS Windows 7 import audit: {count} static imports; OS/subsystem <= 6.1")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("exe")
    arguments = parser.parse_args()
    try:
        verify(arguments.exe)
    except (ValueError, IndexError, struct.error) as error:
        parser.exit(1, f"FAIL {error}\n")

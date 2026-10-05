#include <windows.h>
#include <tlhelp32.h>
#include <algorithm>
#include <array>
#include <cstdio>
#include <cwctype>
#include <iomanip>
#include <map>
#include <sstream>
#include <share.h>
#include <stdexcept>
#include <string>
#include <vector>

volatile LONG interrupted = 0;

BOOL WINAPI consoleHandler(DWORD event) {
    if (event == CTRL_C_EVENT || event == CTRL_BREAK_EVENT) {
        InterlockedExchange(&interrupted, 1);
        return TRUE;
    }
    return FALSE;
}

std::string hex(ULONG64 value) {
    std::ostringstream stream;
    stream << "0x" << std::hex << value;
    return stream.str();
}

std::string utf8(const std::wstring& value) {
    int size = WideCharToMultiByte(CP_UTF8, 0, value.data(), static_cast<int>(value.size()),
                                   nullptr, 0, nullptr, nullptr);
    std::string result(size, '\0');
    WideCharToMultiByte(CP_UTF8, 0, value.data(), static_cast<int>(value.size()),
                        result.data(), size, nullptr, nullptr);
    return result;
}

std::runtime_error winError(const std::string& action) {
    return std::runtime_error(action + " failed, Windows error=" + std::to_string(GetLastError()));
}

class Log {
    FILE* file = nullptr;
    ULONGLONG start = GetTickCount64();
public:
    explicit Log(const std::wstring& path) {
        file = _wfsopen(path.c_str(), L"wb", _SH_DENYWR);
        if (!file) throw winError("Open output TXT");
        std::fputs("\xef\xbb\xbf", file);
    }
    ~Log() { if (file) std::fclose(file); }
    void write(const std::string& message) {
        std::fprintf(file, "[%llu ms] %s\n", GetTickCount64() - start, message.c_str());
        std::fflush(file);
    }
};

struct Module {
    ULONG64 base;
    DWORD size;
    std::wstring name;
};

struct Api {
    const wchar_t* library;
    const char* name;
    ULONG64 address = 0;
    size_t calls = 0;
};

struct Thread {
    HANDLE handle;
    CONTEXT original;
    bool suspended = false;
};

class Capture {
    DWORD pid;
    HANDLE process = nullptr;
    Log& log;
    bool attached = false;
    bool exited = false;
    bool initialBreakpoint = false;
    bool ready = false;
    bool eventPending = false;
    DEBUG_EVENT pendingEvent{};
#ifdef PCSTORY_TEST_FAULTS
    bool restoreFault = true;
#endif
    std::map<DWORD, Thread> threads;
    std::vector<Module> modules;
    std::array<Api, 4> apis{{{L"user32.dll", "SendMessageW"}, {L"user32.dll", "PostMessageW"},
                             {L"ws2_32.dll", "send"}, {L"ws2_32.dll", "WSASend"}}};

    bool read(ULONG64 address, void* destination, size_t size) const {
        SIZE_T received = 0;
        return address && ReadProcessMemory(process, reinterpret_cast<LPCVOID>(address),
                                            destination, size, &received) && received == size;
    }

    std::string remoteString(ULONG64 address) const {
        std::string result;
        for (size_t offset = 0; offset < 256; ++offset) {
            char character = 0;
            if (!read(address + offset, &character, 1)) return {};
            if (!character) return result;
            result += character;
        }
        return {};
    }

    ULONG64 exportAddress(const std::wstring& library, const std::string& name, int depth = 0) const {
        if (depth > 4) return 0;
        auto found = std::find_if(modules.begin(), modules.end(), [&](const Module& module) {
            return _wcsicmp(module.name.c_str(), library.c_str()) == 0;
        });
        if (found == modules.end()) return 0;
        IMAGE_DOS_HEADER dos{};
        IMAGE_NT_HEADERS64 nt{};
        if (!read(found->base, &dos, sizeof(dos)) || dos.e_magic != IMAGE_DOS_SIGNATURE ||
            dos.e_lfanew <= 0 || static_cast<DWORD>(dos.e_lfanew) > found->size ||
            !read(found->base + dos.e_lfanew, &nt, sizeof(nt)) || nt.Signature != IMAGE_NT_SIGNATURE ||
            nt.OptionalHeader.Magic != IMAGE_NT_OPTIONAL_HDR64_MAGIC) return 0;
        const auto directory = nt.OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_EXPORT];
        IMAGE_EXPORT_DIRECTORY exports{};
        if (!directory.VirtualAddress || !read(found->base + directory.VirtualAddress, &exports, sizeof(exports)) ||
            exports.NumberOfFunctions > 100000 || exports.NumberOfNames > 100000) return 0;
        DWORD functionIndex = DWORD(-1);
        if (!name.empty() && name[0] == '#') {
            DWORD ordinal = static_cast<DWORD>(std::stoul(name.substr(1)));
            if (ordinal >= exports.Base) functionIndex = ordinal - exports.Base;
        } else {
            std::vector<DWORD> names(exports.NumberOfNames);
            std::vector<WORD> ordinals(exports.NumberOfNames);
            if (names.empty() || !read(found->base + exports.AddressOfNames, names.data(), names.size() * sizeof(DWORD)) ||
                !read(found->base + exports.AddressOfNameOrdinals, ordinals.data(), ordinals.size() * sizeof(WORD))) return 0;
            for (size_t index = 0; index < names.size(); ++index) {
                if (remoteString(found->base + names[index]) == name) {
                    functionIndex = ordinals[index];
                    break;
                }
            }
        }
        DWORD functionRva = 0;
        if (functionIndex >= exports.NumberOfFunctions ||
            !read(found->base + exports.AddressOfFunctions + functionIndex * sizeof(DWORD), &functionRva, sizeof(functionRva)) ||
            !functionRva || functionRva >= found->size) return 0;
        if (functionRva >= directory.VirtualAddress &&
            static_cast<ULONG64>(functionRva) < static_cast<ULONG64>(directory.VirtualAddress) + directory.Size) {
            const std::string forwarder = remoteString(found->base + functionRva);
            const auto separator = forwarder.find_last_of('.');
            if (separator == std::string::npos) return 0;
            const std::string target = forwarder.substr(0, separator);
            std::wstring targetLibrary(target.begin(), target.end());
            if (targetLibrary.find(L'.') == std::wstring::npos) targetLibrary += L".dll";
            return exportAddress(targetLibrary, forwarder.substr(separator + 1), depth + 1);
        }
        return found->base + functionRva;
    }

    void updateModules() {
        HANDLE snapshot = INVALID_HANDLE_VALUE;
        for (int attempt = 0; attempt < 5; ++attempt) {
            snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid);
            if (snapshot != INVALID_HANDLE_VALUE || GetLastError() != ERROR_BAD_LENGTH) break;
        }
        if (snapshot == INVALID_HANDLE_VALUE) throw winError("Snapshot target modules");
        std::vector<Module> current;
        MODULEENTRY32W entry{};
        entry.dwSize = sizeof(entry);
        if (Module32FirstW(snapshot, &entry)) {
            do {
                current.push_back({reinterpret_cast<ULONG64>(entry.modBaseAddr), entry.modBaseSize, entry.szModule});
            } while (Module32NextW(snapshot, &entry));
        }
        CloseHandle(snapshot);
        modules = std::move(current);
        bool changed = false;
        for (auto& api : apis) {
            ULONG64 address = exportAddress(api.library, api.name);
            if (api.address != address) {
                api.address = address;
                changed = true;
                log.write(std::string("RESOLVED API=") + api.name + " address=" + hex(address));
            }
        }
        if (changed) {
            for (auto& item : threads) arm(item.second);
        }
        if (!ready && std::all_of(apis.begin(), apis.end(), [](const Api& api) { return api.address != 0; })) {
            log.write("CAPTURE_READY APIs=4");
            std::puts("已开始捕获。请在 PCStory 中操作一次下载；Ctrl+C 可提前结束。");
            ready = true;
        }
    }

    void arm(Thread& thread) {
        CONTEXT context{};
        context.ContextFlags = CONTEXT_DEBUG_REGISTERS;
        if (!GetThreadContext(thread.handle, &context)) throw winError("Get thread debug registers");
        context.Dr0 = apis[0].address;
        context.Dr1 = apis[1].address;
        context.Dr2 = apis[2].address;
        context.Dr3 = apis[3].address;
        context.Dr7 = thread.original.Dr7 & ~0xffff00ffULL;
        for (size_t index = 0; index < apis.size(); ++index) {
            if (apis[index].address) context.Dr7 |= 1ULL << (index * 2);
        }
        context.Dr6 &= ~0xfULL;
        if (!SetThreadContext(thread.handle, &context)) throw winError("Set thread hardware breakpoints");
    }

    void addThread(DWORD id) {
        if (threads.count(id)) return;
        HANDLE handle = OpenThread(THREAD_GET_CONTEXT | THREAD_SET_CONTEXT | THREAD_SUSPEND_RESUME | SYNCHRONIZE,
                                   FALSE, id);
        if (!handle) throw winError("Open target thread");
        CONTEXT original{};
        original.ContextFlags = CONTEXT_DEBUG_REGISTERS;
        if (!GetThreadContext(handle, &original)) {
            auto error = winError("Read original thread registers");
            CloseHandle(handle);
            throw error;
        }
        if (original.Dr7 & 0xff) {
            CloseHandle(handle);
            throw std::runtime_error("Target has occupied hardware breakpoint slots; detach the other debugger first");
        }
        auto inserted = threads.emplace(id, Thread{handle, original});
        arm(inserted.first->second);
    }

    std::string buffer(ULONG64 address, ULONG64 length) const {
        const size_t requested = static_cast<size_t>(std::min<ULONG64>(length, 256));
        std::vector<unsigned char> bytes(requested);
        SIZE_T received = 0;
        if (requested && (!address || !ReadProcessMemory(process, reinterpret_cast<LPCVOID>(address),
                bytes.data(), requested, &received))) {
            if (!received) return " unreadable=true";
        }
        std::ostringstream stream;
        stream << " bytes=" << received << " hex=" << std::hex << std::setfill('0');
        for (size_t index = 0; index < received; ++index) stream << std::setw(2) << unsigned(bytes[index]);
        if (length > received) stream << " truncated=true";
        return stream.str();
    }

    std::string caller(ULONG64 address) const {
        for (const auto& module : modules) {
            if (address >= module.base && address - module.base < module.size) {
                return utf8(module.name) + "+" + hex(address - module.base);
            }
        }
        return hex(address);
    }

    void record(size_t index, DWORD threadId, const CONTEXT& context) {
#ifdef PCSTORY_TEST_FAULTS
        throw std::runtime_error("Injected API capture failure");
#endif
        auto& api = apis[index];
        ++api.calls;
        const size_t limit = index < 2 ? 5000 : 10000;
        if (api.calls == limit + 1) log.write(std::string("LIMIT API=") + api.name + " further calls omitted");
        if (api.calls > limit) return;
        ULONG64 returnAddress = 0;
        read(context.Rsp, &returnAddress, sizeof(returnAddress));
        std::string line = std::string("API=") + api.name + " tid=" + std::to_string(threadId) +
            " caller=" + caller(returnAddress);
        if (index < 2) {
            line += " hwnd=" + hex(context.Rcx) + " msg=" + hex(DWORD(context.Rdx)) +
                " wparam=" + hex(context.R8) + " lparam=" + hex(context.R9);
            if (DWORD(context.Rdx) == WM_COPYDATA) {
                struct RemoteCopyData { ULONG64 tag; DWORD size; DWORD padding; ULONG64 data; } data{};
                if (read(context.R9, &data, sizeof(data))) {
                    line += " copydata.tag=" + hex(data.tag) + " copydata.len=" + std::to_string(data.size) +
                        buffer(data.data, data.size);
                } else line += " copydata.unreadable=true";
            }
        } else if (index == 2) {
            const int length = static_cast<int>(context.R8);
            line += " socket=" + hex(context.Rcx) + " len=" + std::to_string(length) + " flags=" + hex(DWORD(context.R9));
            if (length > 0) line += buffer(context.Rdx, static_cast<ULONG64>(length));
        } else {
            const DWORD count = DWORD(context.R8);
            line += " socket=" + hex(context.Rcx) + " bufferCount=" + std::to_string(count);
            for (DWORD bufferIndex = 0; bufferIndex < std::min<DWORD>(count, 4); ++bufferIndex) {
                struct RemoteBuffer { DWORD size; DWORD padding; ULONG64 data; } data{};
                if (!read(context.Rdx + bufferIndex * sizeof(data), &data, sizeof(data))) {
                    line += " buffer.unreadable=true";
                    break;
                }
                line += " buffer[" + std::to_string(bufferIndex) + "].len=" + std::to_string(data.size) +
                    buffer(data.data, data.size);
            }
            if (count > 4) line += " remainingBuffersOmitted=true";
        }
        log.write(line);
    }

    DWORD handleEvent(const DEBUG_EVENT& event) {
        switch (event.dwDebugEventCode) {
        case CREATE_PROCESS_DEBUG_EVENT:
            if (event.u.CreateProcessInfo.hFile) CloseHandle(event.u.CreateProcessInfo.hFile);
            updateModules();
            addThread(event.dwThreadId);
            break;
        case CREATE_THREAD_DEBUG_EVENT:
            addThread(event.dwThreadId);
            break;
        case EXIT_THREAD_DEBUG_EVENT: {
            auto found = threads.find(event.dwThreadId);
            if (found != threads.end()) {
                CloseHandle(found->second.handle);
                threads.erase(found);
            }
            break;
        }
        case LOAD_DLL_DEBUG_EVENT:
            if (event.u.LoadDll.hFile) CloseHandle(event.u.LoadDll.hFile);
            updateModules();
            break;
        case UNLOAD_DLL_DEBUG_EVENT:
            updateModules();
            break;
        case EXIT_PROCESS_DEBUG_EVENT:
            exited = true;
            log.write("TARGET_EXIT code=" + std::to_string(event.u.ExitProcess.dwExitCode));
            break;
        case EXCEPTION_DEBUG_EVENT: {
            const auto& exception = event.u.Exception.ExceptionRecord;
            if (exception.ExceptionCode == EXCEPTION_BREAKPOINT && !initialBreakpoint) {
                initialBreakpoint = true;
                return DBG_CONTINUE;
            }
            if (exception.ExceptionCode == EXCEPTION_SINGLE_STEP) {
                auto found = threads.find(event.dwThreadId);
                if (found == threads.end()) return DBG_EXCEPTION_NOT_HANDLED;
                CONTEXT context{};
                context.ContextFlags = CONTEXT_FULL | CONTEXT_DEBUG_REGISTERS;
                if (!GetThreadContext(found->second.handle, &context)) throw winError("Read API argument registers");
                DWORD64 handled = 0;
                for (size_t index = 0; index < apis.size(); ++index) {
                    if ((context.Dr6 & (1ULL << index)) && apis[index].address && context.Rip == apis[index].address) {
                        record(index, event.dwThreadId, context);
                        handled |= 1ULL << index;
                    }
                }
                if (handled) {
                    context.Dr6 &= ~handled;
                    context.EFlags |= 0x10000;
                    if (!SetThreadContext(found->second.handle, &context)) throw winError("Resume captured API");
                    return (context.Dr6 & 0xe00f) ? DBG_EXCEPTION_NOT_HANDLED : DBG_CONTINUE;
                }
            }
            log.write("TARGET_EXCEPTION code=" + hex(exception.ExceptionCode) +
                " firstChance=" + std::to_string(event.u.Exception.dwFirstChance));
            return DBG_EXCEPTION_NOT_HANDLED;
        }
        }
        return DBG_CONTINUE;
    }

public:
    Capture(DWORD target, Log& output) : pid(target), log(output) {
        process = OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ | SYNCHRONIZE, FALSE, pid);
        if (!process) throw winError("Open PCStory (try running as administrator)");
    }

    ~Capture() {
        bool warned = false;
        while (!finish()) {
            if (!warned) {
                std::puts("正在重试恢复断点，请勿关闭窗口；恢复完成后会自动退出。");
                warned = true;
            }
            Sleep(100);
        }
        for (auto& item : threads) CloseHandle(item.second.handle);
        if (process) CloseHandle(process);
    }

    void run(DWORD seconds) {
        BOOL wow64 = FALSE;
        if (!IsWow64Process(process, &wow64)) throw winError("Check target architecture");
        if (wow64) throw std::runtime_error("Only Windows x64 targets are supported");
        BOOL debugged = FALSE;
        if (!CheckRemoteDebuggerPresent(process, &debugged)) throw winError("Check existing debugger");
        if (debugged) throw std::runtime_error("Target already has a debugger; detach x64dbg before running this tool");
        if (!DebugActiveProcess(pid)) throw winError("Attach debugger (try running as administrator)");
        attached = true;
        if (!DebugSetProcessKillOnExit(FALSE)) throw winError("Disable target termination on debugger exit");
        log.write("ATTACHED pid=" + std::to_string(pid) + " seconds=" + std::to_string(seconds) + " killOnExit=false");
        const ULONGLONG deadline = GetTickCount64() + static_cast<ULONGLONG>(seconds) * 1000;
        while (!exited && !InterlockedCompareExchange(&interrupted, 0, 0) && GetTickCount64() < deadline) {
            DEBUG_EVENT event{};
            if (!WaitForDebugEvent(&event, 100)) {
                if (GetLastError() == ERROR_SEM_TIMEOUT) continue;
                throw winError("Wait for debug event");
            }
            pendingEvent = event;
            eventPending = true;
            DWORD status = handleEvent(event);
            if (!ContinueDebugEvent(event.dwProcessId, event.dwThreadId, status)) throw winError("Continue target process");
            eventPending = false;
        }
        log.write(InterlockedCompareExchange(&interrupted, 0, 0) ? "STOP reason=Ctrl+C" : "STOP reason=timeout_or_exit");
        if (!finish()) throw std::runtime_error("Could not fully restore/detach; see TXT for details");
        size_t calls = 0;
        for (const auto& api : apis) {
            calls += api.calls;
            log.write(std::string("TOTAL API=") + api.name + " calls=" + std::to_string(api.calls));
        }
        if (!calls) throw std::runtime_error("No API calls captured; verify the PID and trigger a download during capture");
    }

    bool finish() {
        if (!attached) return true;
        bool restored = true;
        if (!exited && WaitForSingleObject(process, 0) != WAIT_OBJECT_0) {
            for (auto& item : threads) {
                auto& thread = item.second;
                if (WaitForSingleObject(thread.handle, 0) == WAIT_OBJECT_0 || thread.suspended) continue;
                if (SuspendThread(thread.handle) == DWORD(-1)) {
                    restored = false;
                    log.write("RESTORE_ERROR tid=" + std::to_string(item.first) + " action=suspend error=" + std::to_string(GetLastError()));
                } else thread.suspended = true;
            }
            for (auto& item : threads) {
                auto& thread = item.second;
                if (!thread.suspended) continue;
#ifdef PCSTORY_TEST_FAULTS
                if (restoreFault) {
                    restoreFault = false;
                    restored = false;
                    log.write("RESTORE_RETRY injected=true");
                    continue;
                }
#endif
                if (!SetThreadContext(thread.handle, &thread.original)) {
                    restored = false;
                    log.write("RESTORE_ERROR tid=" + std::to_string(item.first) + " action=registers error=" + std::to_string(GetLastError()));
                }
            }
            if (!restored) return false;
            for (;;) {
                DEBUG_EVENT event{};
                bool fromPending = eventPending;
                if (fromPending) event = pendingEvent;
                else if (!WaitForDebugEvent(&event, 0)) {
                    if (GetLastError() != ERROR_SEM_TIMEOUT) {
                        log.write("DRAIN_ERROR error=" + std::to_string(GetLastError()));
                        return false;
                    }
                    break;
                }
                if (!fromPending) {
                    pendingEvent = event;
                    eventPending = true;
                    if (event.dwDebugEventCode == LOAD_DLL_DEBUG_EVENT && event.u.LoadDll.hFile)
                        CloseHandle(event.u.LoadDll.hFile);
                    if (event.dwDebugEventCode == CREATE_PROCESS_DEBUG_EVENT && event.u.CreateProcessInfo.hFile)
                        CloseHandle(event.u.CreateProcessInfo.hFile);
                }
                DWORD status = DBG_CONTINUE;
                if (event.dwDebugEventCode == EXCEPTION_DEBUG_EVENT) {
                    status = DBG_EXCEPTION_NOT_HANDLED;
                    const auto& exception = event.u.Exception.ExceptionRecord;
                    if (exception.ExceptionCode == EXCEPTION_BREAKPOINT && !initialBreakpoint) {
                        initialBreakpoint = true;
                        status = DBG_CONTINUE;
                    }
                    if (exception.ExceptionCode == EXCEPTION_SINGLE_STEP) {
                        const auto address = reinterpret_cast<ULONG64>(exception.ExceptionAddress);
                        for (const auto& api : apis) {
                            if (api.address && api.address == address) status = DBG_CONTINUE;
                        }
                    }
                }
                if (!ContinueDebugEvent(event.dwProcessId, event.dwThreadId, status)) {
                    log.write("DRAIN_ERROR action=continue error=" + std::to_string(GetLastError()));
                    return false;
                }
                eventPending = false;
            }
            if (!DebugActiveProcessStop(pid)) {
                log.write("DETACH_ERROR error=" + std::to_string(GetLastError()));
                return false;
            }
            for (auto& item : threads) {
                auto& thread = item.second;
                if (thread.suspended) {
                    if (ResumeThread(thread.handle) == DWORD(-1)) {
                        if (WaitForSingleObject(thread.handle, 0) == WAIT_OBJECT_0) {
                            thread.suspended = false;
                            continue;
                        }
                        log.write("RESTORE_ERROR tid=" + std::to_string(item.first) + " action=resume error=" + std::to_string(GetLastError()));
                        return false;
                    }
                    thread.suspended = false;
                }
            }
        } else if (eventPending) {
            ContinueDebugEvent(pendingEvent.dwProcessId, pendingEvent.dwThreadId, DBG_CONTINUE);
            eventPending = false;
        }
        attached = false;
        log.write(std::string("DETACHED restored=") + (restored ? "true" : "false"));
        return restored;
    }
};

DWORD findPcstory() {
    HANDLE snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
    if (snapshot == INVALID_HANDLE_VALUE) throw winError("List running processes");
    std::vector<DWORD> matches;
    PROCESSENTRY32W entry{};
    entry.dwSize = sizeof(entry);
    if (Process32FirstW(snapshot, &entry)) {
        do {
            if (_wcsicmp(entry.szExeFile, L"pcstory.exe") == 0) matches.push_back(entry.th32ProcessID);
        } while (Process32NextW(snapshot, &entry));
    }
    CloseHandle(snapshot);
    if (matches.empty()) throw std::runtime_error("pcstory.exe is not running; start PCStory first");
    if (matches.size() != 1) throw std::runtime_error("Multiple PCStory processes found; use --pid with the intended process ID");
    return matches.front();
}

std::wstring defaultOutput() {
    std::vector<wchar_t> path(32768);
    DWORD size = GetModuleFileNameW(nullptr, path.data(), static_cast<DWORD>(path.size()));
    if (!size || size >= path.size()) throw winError("Locate debugger executable");
    std::wstring output(path.data(), size);
    return output.substr(0, output.find_last_of(L"\\/") + 1) + L"pcstory-debug.txt";
}

DWORD number(const wchar_t* text, DWORD minimum, DWORD maximum) {
    size_t consumed = 0;
    const std::wstring value(text);
    unsigned long long parsed = std::stoull(value, &consumed, 10);
    if (consumed != value.size() || parsed < minimum || parsed > maximum) throw std::runtime_error("Argument is out of range");
    return static_cast<DWORD>(parsed);
}

int wmain(int argc, wchar_t** argv) {
    SetConsoleOutputCP(CP_UTF8);
    SetConsoleCtrlHandler(consoleHandler, TRUE);
    bool pause = true;
    for (int index = 1; index < argc; ++index) if (std::wstring(argv[index]) == L"--no-pause") pause = false;
    int result = 0;
    std::wstring output;
    try {
        output = defaultOutput();
        DWORD pid = 0;
        DWORD seconds = 90;
        for (int index = 1; index < argc; ++index) {
            const std::wstring argument(argv[index]);
            if (argument == L"--no-pause") continue;
            if (argument == L"--help") {
                std::puts("PcstoryDebugger.exe [--pid ID] [--seconds 5..600] [--output TXT] [--no-pause]");
                return 0;
            }
            if (index + 1 >= argc) throw std::runtime_error("Missing argument value");
            if (argument == L"--pid") pid = number(argv[++index], 1, MAXDWORD);
            else if (argument == L"--seconds") seconds = number(argv[++index], 5, 600);
            else if (argument == L"--output") output = argv[++index];
            else throw std::runtime_error("Unknown argument: " + utf8(argument));
        }
        Log log(output);
        log.write("PcstoryDebugger v0.1 Windows x64; buffers limited to 256 bytes; observational capture only");
        try {
            if (!pid) pid = findPcstory();
            HANDLE privilegeToken = nullptr;
            if (OpenProcessToken(GetCurrentProcess(), TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY, &privilegeToken)) {
                TOKEN_PRIVILEGES privileges{};
                privileges.PrivilegeCount = 1;
                if (LookupPrivilegeValueW(nullptr, SE_DEBUG_NAME, &privileges.Privileges[0].Luid)) {
                    privileges.Privileges[0].Attributes = SE_PRIVILEGE_ENABLED;
                    AdjustTokenPrivileges(privilegeToken, FALSE, &privileges, 0, nullptr, nullptr);
                }
                CloseHandle(privilegeToken);
            }
            Capture capture(pid, log);
            capture.run(seconds);
        } catch (const std::exception& error) {
            log.write(std::string("ERROR ") + error.what());
            throw;
        }
        std::puts("捕获结束，已恢复线程断点并退出调试。请发送 pcstory-debug.txt。");
    } catch (const std::exception& error) {
        std::fprintf(stderr, "失败：%s\n请先启动 PCStory、关闭 x64dbg，并以管理员身份运行本工具。\n", error.what());
        result = 1;
    }
    if (!output.empty()) std::printf("TXT 路径：%s\n", utf8(output).c_str());
    if (pause) {
        std::puts("按回车关闭窗口。");
        std::getchar();
    }
    return result;
}

#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>
#include <tlhelp32.h>
#include <iphlpapi.h>
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

struct PendingCall {
    size_t api;
    size_t id;
    ULONG64 address;
    ULONG64 stack;
    ULONG64 socket;
    ULONG64 buffer;
    int requested;
};

struct Thread {
    HANDLE handle;
    CONTEXT original;
    bool suspended = false;
    std::vector<PendingCall> pending;
};

class Capture {
    DWORD pid;
    HANDLE process = nullptr;
    Log& log;
    bool attached = false;
    bool hadAttached = false;
    bool detachReported = false;
    bool exited = false;
    bool initialBreakpoint = false;
    bool ready = false;
    bool eventPending = false;
    bool continuationKnown = false;
    DWORD pendingStatus = DBG_CONTINUE;
    bool networkMode = false;
    DEBUG_EVENT pendingEvent{};
#ifdef PCSTORY_TEST_FAULTS
    bool restoreFault = true;
#endif
    std::map<DWORD, Thread> threads;
    std::vector<Module> modules;
    std::array<Api, 4> apis{{{L"user32.dll", "SendMessageW"}, {L"user32.dll", "PostMessageW"},
                             {L"ws2_32.dll", "send"}, {L"ws2_32.dll", "WSASend"}}};

    size_t entryCount() const { return networkMode ? 3 : 4; }

    ULONG64 slotAddress(size_t index, const Thread& thread) const {
        if (networkMode && index == 3)
            return thread.pending.empty() ? 0 : thread.pending.back().address;
        return apis[index].address;
    }

    void setBreakpoints(CONTEXT& context, const Thread& thread) const {
        context.Dr0 = slotAddress(0, thread);
        context.Dr1 = slotAddress(1, thread);
        context.Dr2 = slotAddress(2, thread);
        context.Dr3 = slotAddress(3, thread);
        context.Dr7 = thread.original.Dr7 & ~0xffff00ffULL;
        for (size_t index = 0; index < 4; ++index) {
            if (slotAddress(index, thread)) context.Dr7 |= 1ULL << (index * 2);
        }
    }

    ULONG64 ownedBreakpoints(const CONTEXT& context, const Thread& thread, ULONG64 address) const {
        ULONG64 owned = 0;
        const ULONG64 registers[] = {context.Dr0, context.Dr1, context.Dr2, context.Dr3};
        for (size_t index = 0; index < 4; ++index) {
            if ((context.Dr6 & (1ULL << index)) && (context.Dr7 & (3ULL << (index * 2))) &&
                address && slotAddress(index, thread) == address && registers[index] == address)
                owned |= 1ULL << index;
        }
        return owned;
    }

    void abandon(Thread& thread, DWORD id, const std::string& reason) {
        for (const auto& call : thread.pending) {
            log.write(std::string("RETURN_UNOBSERVED API=") + apis[call.api].name + " tid=" +
                std::to_string(id) + " call=" + std::to_string(call.id) + " reason=" + reason);
        }
        thread.pending.clear();
    }

    void trackReturn(size_t index, DWORD id, Thread& thread, const CONTEXT& context) {
        if (!networkMode || apis[index].calls > (index < 2 ? 5000 : 10000)) return;
        while (!thread.pending.empty() && context.Rsp >= thread.pending.back().stack) {
            const auto& call = thread.pending.back();
            log.write(std::string("RETURN_UNOBSERVED API=") + apis[call.api].name + " tid=" +
                std::to_string(id) + " call=" + std::to_string(call.id) + " reason=stack_unwound");
            thread.pending.pop_back();
        }
        ULONG64 address = 0;
        if (!read(context.Rsp, &address, sizeof(address)) || !address || thread.pending.size() >= 16) {
            log.write("RETURN_UNOBSERVED tid=" + std::to_string(id) + " reason=unreadable_or_nesting_limit");
            return;
        }
        thread.pending.push_back({index, apis[index].calls, address, context.Rsp, context.Rcx,
                                  context.Rdx, static_cast<int>(context.R8)});
    }

    void recordReturn(DWORD id, Thread& thread, const CONTEXT& context) {
        const auto call = thread.pending.back();
        if (context.Rsp != call.stack + sizeof(ULONG64)) {
            log.write("RETURN_SKIPPED tid=" + std::to_string(id) + " reason=stack_mismatch");
            return;
        }
        const LONG result = static_cast<LONG>(static_cast<DWORD>(context.Rax));
        std::string line = std::string("RETURN API=") + apis[call.api].name + " tid=" + std::to_string(id) +
            " call=" + std::to_string(call.id) + " socket=" + hex(call.socket) + " result=" + std::to_string(result);
        if (result == SOCKET_ERROR) line += " failed=true target_wsa_error=not_captured";
        if (call.api == 2 && result > 0 && call.requested > 0)
            line += buffer(call.buffer, std::min<ULONG64>(result, call.requested));
        if (call.api == 2 && result == 0) line += " zero_length_receive=true";
        log.write(line);
        thread.pending.pop_back();
    }

    std::string processPath(DWORD owner) const {
        HANDLE handle = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, owner);
        if (!handle) return "unavailable(error=" + std::to_string(GetLastError()) + ")";
        std::vector<wchar_t> path(32768);
        DWORD size = static_cast<DWORD>(path.size());
        const BOOL success = QueryFullProcessImageNameW(handle, 0, path.data(), &size);
        const DWORD error = success ? 0 : GetLastError();
        CloseHandle(handle);
        return success ? utf8(std::wstring(path.data(), size)) : "unavailable(error=" + std::to_string(error) + ")";
    }

    std::string tcpEndpoint(DWORD address, DWORD port) const {
        IN_ADDR ipv4{};
        ipv4.S_un.S_addr = address;
        char host[INET_ADDRSTRLEN]{};
        if (!InetNtopA(AF_INET, &ipv4, host, sizeof(host))) return "unavailable";
        return std::string(host) + ":" + std::to_string(ntohs(static_cast<u_short>(port)));
    }

    void scanPorts(const char* phase) {
        if (!networkMode) return;
        log.write(std::string("PORT_SCAN phase=") + phase + " family=IPv4");
        DWORD size = 0;
        DWORD status = GetExtendedTcpTable(nullptr, &size, FALSE, AF_INET, TCP_TABLE_OWNER_PID_ALL, 0);
        std::vector<unsigned char> storage;
        for (int attempt = 0; status == ERROR_INSUFFICIENT_BUFFER && attempt < 4; ++attempt) {
            storage.resize(size);
            status = GetExtendedTcpTable(storage.data(), &size, FALSE, AF_INET, TCP_TABLE_OWNER_PID_ALL, 0);
        }
        if (status != NO_ERROR || storage.empty()) {
            log.write("PORT_SCAN_ERROR error=" + std::to_string(status));
            return;
        }
        const auto* table = reinterpret_cast<const MIB_TCPTABLE_OWNER_PID*>(storage.data());
        size_t matches = 0;
        std::map<DWORD, std::string> paths;
        for (DWORD index = 0; index < table->dwNumEntries; ++index) {
            const auto& row = table->table[index];
            const auto port = ntohs(static_cast<u_short>(row.dwLocalPort));
            const bool listener = row.dwState == MIB_TCP_STATE_LISTEN;
            if (row.dwOwningPid != pid && !(listener && (port == 12000 || port == 12200))) continue;
            if (!paths.count(row.dwOwningPid)) paths[row.dwOwningPid] = processPath(row.dwOwningPid);
            log.write("TCP_OWNER pid=" + std::to_string(row.dwOwningPid) + " state=" + std::to_string(row.dwState) +
                " local=" + tcpEndpoint(row.dwLocalAddr, row.dwLocalPort) +
                " remote=" + (listener ? std::string("none") : tcpEndpoint(row.dwRemoteAddr, row.dwRemotePort)) +
                " path=" + paths[row.dwOwningPid]);
            ++matches;
        }
        log.write("PORT_SCAN_END matches=" + std::to_string(matches));
    }

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
        for (size_t index = 0; index < entryCount(); ++index) {
            auto& api = apis[index];
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
        if (!ready && std::all_of(apis.begin(), apis.begin() + entryCount(), [](const Api& api) { return api.address != 0; })) {
            log.write("CAPTURE_READY APIs=" + std::to_string(entryCount()) + " mode=" + (networkMode ? "network returns=true async=false" : "ui"));
            std::puts(networkMode ? "已开始网络捕获。请启动或操作 PCStory；Ctrl+C 可提前结束。" :
                                   "已开始捕获。请在 PCStory 中操作一次下载；Ctrl+C 可提前结束。");
            ready = true;
        }
    }

    void arm(Thread& thread) {
        CONTEXT context{};
        context.ContextFlags = CONTEXT_DEBUG_REGISTERS;
        if (!GetThreadContext(thread.handle, &context)) throw winError("Get thread debug registers");
        setBreakpoints(context, thread);
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
        log.write("THREAD_SAVED tid=" + std::to_string(id) + " dr0=" + hex(original.Dr0) +
            " dr1=" + hex(original.Dr1) + " dr2=" + hex(original.Dr2) + " dr3=" + hex(original.Dr3) +
            " dr7=" + hex(original.Dr7));
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

    std::string socketAddress(ULONG64 address, ULONG64 length) const {
        sockaddr_storage storage{};
        const size_t amount = static_cast<size_t>(std::min<ULONG64>(length, sizeof(storage)));
        if (!amount || !read(address, &storage, amount)) return "unreadable=true";
        char host[INET6_ADDRSTRLEN]{};
        if (storage.ss_family == AF_INET && amount >= sizeof(sockaddr_in)) {
            const auto* ipv4 = reinterpret_cast<const sockaddr_in*>(&storage);
            if (!InetNtopA(AF_INET, &ipv4->sin_addr, host, sizeof(host)))
                return "family=2 format_error=" + std::to_string(WSAGetLastError());
            return std::string("addr=") + host + ":" + std::to_string(ntohs(ipv4->sin_port));
        }
        if (storage.ss_family == AF_INET6 && amount >= sizeof(sockaddr_in6)) {
            const auto* ipv6 = reinterpret_cast<const sockaddr_in6*>(&storage);
            if (!InetNtopA(AF_INET6, &ipv6->sin6_addr, host, sizeof(host)))
                return "family=23 format_error=" + std::to_string(WSAGetLastError());
            return std::string("addr=[") + host + "]:" + std::to_string(ntohs(ipv6->sin6_port));
        }
        return "family=" + std::to_string(storage.ss_family) + " unsupported=true";
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
        if (networkMode) {
            line += " call=" + std::to_string(api.calls);
            if (index == 0) {
                line += " socket=" + hex(context.Rcx) + " sockaddr=" + hex(context.Rdx) +
                    " addrlen=" + std::to_string(DWORD(context.R8)) + " " +
                    socketAddress(context.Rdx, context.R8);
            } else if (index == 1) {
                const int length = static_cast<int>(context.R8);
                line += " socket=" + hex(context.Rcx) + " len=" + std::to_string(length) +
                    " flags=" + hex(DWORD(context.R9));
                if (length > 0) line += buffer(context.Rdx, static_cast<ULONG64>(length));
            } else if (index == 2) {
                line += " socket=" + hex(context.Rcx) + " buffer=" + hex(context.Rdx) +
                    " requested=" + std::to_string(DWORD(context.R8)) + " flags=" + hex(DWORD(context.R9)) +
                    " entry_buffer_not_return_data=true";
            } else {
                line += " socket=" + hex(context.Rcx) + " buffers=" + hex(context.Rdx) +
                    " bufferCount=" + std::to_string(DWORD(context.R8)) +
                    " entry_buffer_not_return_data=true";
            }
        } else if (index < 2) {
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
                abandon(found->second, event.dwThreadId, "thread_exit");
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
                const DWORD64 handled = ownedBreakpoints(context, found->second, context.Rip);
                for (size_t index = 0; index < entryCount(); ++index) {
                    if (handled & (1ULL << index)) {
                        record(index, event.dwThreadId, context);
                        trackReturn(index, event.dwThreadId, found->second, context);
                    }
                }
                if (networkMode && (handled & 8)) recordReturn(event.dwThreadId, found->second, context);
                if (handled) {
                    setBreakpoints(context, found->second);
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

    bool resumeSuspended() {
        bool resumed = true;
        for (auto& item : threads) {
            auto& thread = item.second;
            if (!thread.suspended) continue;
            if (WaitForSingleObject(thread.handle, 0) == WAIT_OBJECT_0) {
                thread.suspended = false;
                continue;
            }
            if (ResumeThread(thread.handle) == DWORD(-1)) {
                log.write("RESTORE_ERROR tid=" + std::to_string(item.first) + " action=resume error=" + std::to_string(GetLastError()));
                resumed = false;
            } else thread.suspended = false;
        }
        return resumed;
    }

    void reportDetach() {
        if (hadAttached && !detachReported) {
            for (auto& item : threads) abandon(item.second, item.first, "capture_end");
            scanPorts("detach");
            log.write("DETACHED restored=true");
            detachReported = true;
        }
    }

    bool classifyPending() {
        pendingStatus = DBG_CONTINUE;
        if (pendingEvent.dwDebugEventCode == EXCEPTION_DEBUG_EVENT) {
            pendingStatus = DBG_EXCEPTION_NOT_HANDLED;
            const auto& exception = pendingEvent.u.Exception.ExceptionRecord;
            if (exception.ExceptionCode == EXCEPTION_BREAKPOINT && !initialBreakpoint)
                pendingStatus = DBG_CONTINUE;
            if (exception.ExceptionCode == EXCEPTION_SINGLE_STEP) {
                auto found = threads.find(pendingEvent.dwThreadId);
                if (found == threads.end()) {
                    continuationKnown = true;
                    return true;
                }
                CONTEXT context{};
                context.ContextFlags = CONTEXT_DEBUG_REGISTERS;
                if (!GetThreadContext(found->second.handle, &context)) return false;
                const ULONG64 address = reinterpret_cast<ULONG64>(exception.ExceptionAddress);
                const ULONG64 owned = ownedBreakpoints(context, found->second, address);
                if (owned && !(context.Dr6 & 0xe00f & ~owned)) pendingStatus = DBG_CONTINUE;
            }
        }
        continuationKnown = true;
        return true;
    }

    bool drainPending() {
        for (;;) {
            DEBUG_EVENT event{};
            bool fromPending = eventPending;
            if (fromPending) event = pendingEvent;
            else if (!WaitForDebugEvent(&event, 0)) {
                if (GetLastError() != ERROR_SEM_TIMEOUT) {
                    log.write("DRAIN_ERROR error=" + std::to_string(GetLastError()));
                    return false;
                }
                return true;
            }
            if (!fromPending) {
                pendingEvent = event;
                eventPending = true;
                continuationKnown = false;
                if (event.dwDebugEventCode == LOAD_DLL_DEBUG_EVENT && event.u.LoadDll.hFile)
                    CloseHandle(event.u.LoadDll.hFile);
                if (event.dwDebugEventCode == CREATE_PROCESS_DEBUG_EVENT && event.u.CreateProcessInfo.hFile)
                    CloseHandle(event.u.CreateProcessInfo.hFile);
            }
            if (!continuationKnown && !classifyPending()) return false;
            if (event.dwDebugEventCode == EXCEPTION_DEBUG_EVENT &&
                event.u.Exception.ExceptionRecord.ExceptionCode == EXCEPTION_SINGLE_STEP && pendingStatus == DBG_CONTINUE) {
                auto found = threads.find(event.dwThreadId);
                if (found != threads.end() && !found->second.suspended) {
                    CONTEXT context{};
                    context.ContextFlags = CONTEXT_CONTROL | CONTEXT_DEBUG_REGISTERS;
                    if (!GetThreadContext(found->second.handle, &context)) return false;
                    context.EFlags |= 0x10000;
                    context.Dr6 &= ~0xfULL;
                    if (!SetThreadContext(found->second.handle, &context)) return false;
                }
            }
            if (!ContinueDebugEvent(event.dwProcessId, event.dwThreadId, pendingStatus)) {
                log.write("DRAIN_ERROR action=continue error=" + std::to_string(GetLastError()));
                return false;
            }
            if (event.dwDebugEventCode == EXIT_THREAD_DEBUG_EVENT) {
                auto found = threads.find(event.dwThreadId);
                if (found != threads.end()) {
                    CloseHandle(found->second.handle);
                    threads.erase(found);
                }
            }
            if (event.dwDebugEventCode == EXIT_PROCESS_DEBUG_EVENT) exited = true;
            if (event.dwDebugEventCode == EXCEPTION_DEBUG_EVENT &&
                event.u.Exception.ExceptionRecord.ExceptionCode == EXCEPTION_BREAKPOINT && pendingStatus == DBG_CONTINUE)
                initialBreakpoint = true;
            eventPending = false;
        }
    }

public:
    Capture(DWORD target, Log& output, bool network) : pid(target), log(output), networkMode(network) {
        if (networkMode) {
            apis = {{{L"ws2_32.dll", "connect"}, {L"ws2_32.dll", "send"},
                     {L"ws2_32.dll", "recv"}, {L"ws2_32.dll", "WSARecv"}}};
        }
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
        hadAttached = true;
        if (!DebugSetProcessKillOnExit(FALSE)) throw winError("Disable target termination on debugger exit");
        log.write("ATTACHED pid=" + std::to_string(pid) + " seconds=" + std::to_string(seconds) + " killOnExit=false");
        scanPorts("attach");
        const ULONGLONG deadline = GetTickCount64() + static_cast<ULONGLONG>(seconds) * 1000;
        while (!exited && !InterlockedCompareExchange(&interrupted, 0, 0) && GetTickCount64() < deadline) {
            DEBUG_EVENT event{};
            if (!WaitForDebugEvent(&event, 100)) {
                if (GetLastError() == ERROR_SEM_TIMEOUT) continue;
                throw winError("Wait for debug event");
            }
            pendingEvent = event;
            eventPending = true;
            continuationKnown = false;
            if (!classifyPending()) throw winError("Classify debug exception ownership");
            DWORD status = handleEvent(event);
            pendingStatus = status;
            if (!ContinueDebugEvent(event.dwProcessId, event.dwThreadId, status)) throw winError("Continue target process");
            eventPending = false;
        }
        log.write(InterlockedCompareExchange(&interrupted, 0, 0) ? "STOP reason=Ctrl+C" : "STOP reason=timeout_or_exit");
        if (!finish()) throw std::runtime_error("Could not fully restore/detach; see TXT for details");
        size_t calls = 0;
        for (size_t index = 0; index < entryCount(); ++index) {
            const auto& api = apis[index];
            calls += api.calls;
            log.write(std::string("TOTAL API=") + api.name + " calls=" + std::to_string(api.calls));
        }
        if (!calls) throw std::runtime_error("No API calls captured; verify the PID and trigger a download during capture");
    }

    bool finish() {
        if (!attached) {
            if (!resumeSuspended()) return false;
            reportDetach();
            return true;
        }
        if (eventPending && !continuationKnown && !classifyPending()) return false;
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
            if (!drainPending() || !restored) return false;
            for (auto& item : threads) {
                auto& thread = item.second;
                if (!thread.suspended) continue;
                if (WaitForSingleObject(thread.handle, 0) == WAIT_OBJECT_0) {
                    thread.suspended = false;
                    continue;
                }
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
            if (!DebugActiveProcessStop(pid)) {
                log.write("DETACH_ERROR error=" + std::to_string(GetLastError()));
                return false;
            }
            attached = false;
            for (const auto& item : threads) {
                if (!item.second.suspended) continue;
                CONTEXT context{};
                context.ContextFlags = CONTEXT_DEBUG_REGISTERS;
                if (GetThreadContext(item.second.handle, &context)) {
                    log.write("THREAD_AFTER_DETACH tid=" + std::to_string(item.first) + " dr0=" + hex(context.Dr0) +
                        " dr1=" + hex(context.Dr1) + " dr2=" + hex(context.Dr2) + " dr3=" + hex(context.Dr3) +
                        " dr7=" + hex(context.Dr7));
                }
            }
            if (!resumeSuspended()) return false;
        } else if (eventPending) {
            ContinueDebugEvent(pendingEvent.dwProcessId, pendingEvent.dwThreadId, DBG_CONTINUE);
            eventPending = false;
        }
        attached = false;
        if (!resumeSuspended()) return false;
        reportDetach();
        return true;
    }
};

DWORD findPcstory(DWORD waitSeconds) {
    const ULONGLONG deadline = GetTickCount64() + static_cast<ULONGLONG>(waitSeconds) * 1000;
    for (;;) {
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
        if (matches.size() == 1) return matches.front();
        if (matches.size() > 1) throw std::runtime_error("Multiple PCStory processes found; use --pid with the intended process ID");
        if (!waitSeconds || GetTickCount64() >= deadline)
            throw std::runtime_error("pcstory.exe is not running; start PCStory first, or use --wait");
        Sleep(250);
    }
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
    bool network = false;
    bool waitForProcess = false;
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
            if (argument == L"--network") { network = true; continue; }
            if (argument == L"--wait") { waitForProcess = true; continue; }
            if (argument == L"--help") {
                std::puts("PcstoryDebugger.exe [--network] [--wait] [--pid ID] [--seconds 5..600] [--output TXT] [--no-pause]");
                return 0;
            }
            if (index + 1 >= argc) throw std::runtime_error("Missing argument value");
            if (argument == L"--pid") pid = number(argv[++index], 1, MAXDWORD);
            else if (argument == L"--seconds") seconds = number(argv[++index], 5, 600);
            else if (argument == L"--output") output = argv[++index];
            else throw std::runtime_error("Unknown argument: " + utf8(argument));
        }
        Log log(output);
        log.write("PcstoryDebugger v0.2 Windows x64; buffers limited to 256 bytes; observational capture only");
        try {
            if (!pid) {
                if (waitForProcess) std::puts("正在等待 pcstory.exe，请现在启动或重启 PCStory...");
                pid = findPcstory(waitForProcess ? seconds : 0);
            }
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
            log.write(std::string("MODE=") + (network ? "network" : "ui") +
                (waitForProcess ? " wait=true" : " wait=false"));
            Capture capture(pid, log, network);
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

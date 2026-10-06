#include <winsock2.h>
#include <windows.h>
#include <fstream>
#include <string>
#include <thread>

bool exists(const char* path) {
    return GetFileAttributesA(path) != INVALID_FILE_ATTRIBUTES;
}

CONTEXT baseline{};

void marker(const char* path) {
    std::ofstream(path) << "ok\n";
}

LRESULT CALLBACK windowProc(HWND window, UINT message, WPARAM word, LPARAM parameter) {
    return DefWindowProcW(window, message, word, parameter);
}

void produceCalls(HWND window, SOCKET connection) {
    const char payload[] = "PCSTORY_CAPTURE_TEST gid=5131";
    COPYDATASTRUCT copyData{};
    copyData.dwData = 5131;
    copyData.cbData = sizeof(payload);
    copyData.lpData = const_cast<char*>(payload);
    SendMessageW(window, WM_COPYDATA, 0, reinterpret_cast<LPARAM>(&copyData));
    PostMessageW(window, WM_APP + 17, 5131, 42);
    send(connection, payload, sizeof(payload), 0);
    WSABUF buffer{};
    buffer.buf = const_cast<char*>(payload);
    buffer.len = sizeof(payload);
    DWORD sent = 0;
    WSASend(connection, &buffer, 1, &sent, 0, nullptr, nullptr);
}

int main(int argc, char** argv) {
    if (argc == 3 && std::string(argv[1]) == "--interrupt") {
        FreeConsole();
        if (!AttachConsole(static_cast<DWORD>(std::stoul(argv[2])))) return 6;
        SetConsoleCtrlHandler(nullptr, TRUE);
        BOOL sent = GenerateConsoleCtrlEvent(CTRL_C_EVENT, 0);
        FreeConsole();
        return sent ? 0 : 7;
    }
    WSADATA winsock{};
    if (WSAStartup(MAKEWORD(2, 2), &winsock)) return 1;
    SOCKET receiver = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    SOCKET sender = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    const bool network = argc == 2 && std::string(argv[1]) == "--network";
    SOCKET listener = INVALID_SOCKET;
    SOCKET blockedReceiver = INVALID_SOCKET;
    sockaddr_in blockedAddress{};
    std::thread blockedWorker;
    sockaddr_in address{};
    address.sin_family = AF_INET;
    address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    if (bind(receiver, reinterpret_cast<sockaddr*>(&address), sizeof(address))) return 2;
    int addressSize = sizeof(address);
    getsockname(receiver, reinterpret_cast<sockaddr*>(&address), &addressSize);
    if (connect(sender, reinterpret_cast<sockaddr*>(&address), sizeof(address))) return 3;
    if (network) {
        listener = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
        sockaddr_in listenAddress{};
        listenAddress.sin_family = AF_INET;
        listenAddress.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
        if (bind(listener, reinterpret_cast<sockaddr*>(&listenAddress), sizeof(listenAddress)) ||
            listen(listener, 1)) return 11;
        blockedReceiver = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
        blockedAddress = listenAddress;
        if (bind(blockedReceiver, reinterpret_cast<sockaddr*>(&blockedAddress), sizeof(blockedAddress))) return 12;
        int blockedSize = sizeof(blockedAddress);
        getsockname(blockedReceiver, reinterpret_cast<sockaddr*>(&blockedAddress), &blockedSize);
        const DWORD timeout = 10000;
        setsockopt(blockedReceiver, SOL_SOCKET, SO_RCVTIMEO, reinterpret_cast<const char*>(&timeout), sizeof(timeout));
    }
    WNDCLASSW windowClass{};
    windowClass.lpfnWndProc = windowProc;
    windowClass.hInstance = GetModuleHandleW(nullptr);
    windowClass.lpszClassName = L"CaptureFixture";
    RegisterClassW(&windowClass);
    HWND window = CreateWindowW(windowClass.lpszClassName, L"Fixture", 0, 0, 0, 0, 0,
                                nullptr, nullptr, windowClass.hInstance, nullptr);
    if (!window) return 4;
    DWORD mainId = GetCurrentThreadId();
    std::thread seedRegisters([mainId] {
        HANDLE thread = OpenThread(THREAD_GET_CONTEXT | THREAD_SET_CONTEXT | THREAD_SUSPEND_RESUME, FALSE, mainId);
        if (!thread) return;
        if (SuspendThread(thread) == DWORD(-1)) { CloseHandle(thread); return; }
        CONTEXT registers{};
        registers.ContextFlags = CONTEXT_DEBUG_REGISTERS;
        if (GetThreadContext(thread, &registers)) {
            registers.Dr0 = 0x12345678;
            SetThreadContext(thread, &registers);
            baseline.ContextFlags = CONTEXT_DEBUG_REGISTERS;
            GetThreadContext(thread, &baseline);
            std::ofstream report("baseline.txt");
            report << std::hex << "dr0=" << baseline.Dr0 << " dr1=" << baseline.Dr1 << " dr2=" << baseline.Dr2
                   << " dr3=" << baseline.Dr3 << " dr7=" << baseline.Dr7 << '\n';
        }
        ResumeThread(thread);
        CloseHandle(thread);
    });
    seedRegisters.join();
    marker("ready.txt");
    const ULONGLONG deadline = GetTickCount64() + 30000;
    bool produced = false;
    bool checked = false;
    while (!exists("stop.txt") && GetTickCount64() < deadline) {
        MSG message{};
        while (PeekMessageW(&message, nullptr, 0, 0, PM_REMOVE)) {
            DispatchMessageW(&message);
        }
        if (!produced && exists("go.txt")) {
            if (network) {
                if (connect(sender, reinterpret_cast<sockaddr*>(&address), sizeof(address))) return 8;
                const char reply[] = "PCSTORY_REPLY gid=5131";
                if (send(sender, reply, sizeof(reply), 0) != sizeof(reply)) return 9;
                char received[128]{};
                if (recv(receiver, received, sizeof(received), 0) != sizeof(reply)) return 10;
                recv(INVALID_SOCKET, received, sizeof(received), 0);
                connect(INVALID_SOCKET, reinterpret_cast<sockaddr*>(&address), sizeof(address));
                send(INVALID_SOCKET, reply, sizeof(reply), 0);
                if (send(sender, reply, 0, 0) != 0 || recv(receiver, received, sizeof(received), 0) != 0) return 13;
            }
            produceCalls(window, sender);
            std::thread worker([sender] {
                const char data[] = "PCSTORY_NEW_THREAD_TEST";
                send(sender, data, sizeof(data), 0);
            });
            worker.join();
            if (network) {
                blockedWorker = std::thread([blockedReceiver] {
                    char data[16]{};
                    recv(blockedReceiver, data, sizeof(data), 0);
                    marker("blocked-returned.txt");
                });
            }
            marker("calls.txt");
            produced = true;
        }
        if (!checked && exists("check.txt")) {
            DWORD mainThread = GetCurrentThreadId();
            std::thread verifier([mainThread] {
                CONTEXT registers{};
                registers.ContextFlags = CONTEXT_DEBUG_REGISTERS;
                HANDLE thread = OpenThread(THREAD_GET_CONTEXT | THREAD_SUSPEND_RESUME, FALSE, mainThread);
                if (!thread) return;
                if (SuspendThread(thread) == DWORD(-1)) { CloseHandle(thread); return; }
                bool read = GetThreadContext(thread, &registers) != FALSE;
                ResumeThread(thread);
                CloseHandle(thread);
                BOOL debugged = TRUE;
                CheckRemoteDebuggerPresent(GetCurrentProcess(), &debugged);
                std::ofstream report("register-check.txt");
                report << "read=" << read << " debugged=" << debugged << std::hex
                       << " dr0=" << registers.Dr0 << " dr1=" << registers.Dr1 << " dr2=" << registers.Dr2
                       << " dr3=" << registers.Dr3 << " dr7=" << registers.Dr7 << '\n';
                report.close();
                if (read && !debugged && registers.Dr0 == baseline.Dr0 && registers.Dr1 == baseline.Dr1 &&
                    registers.Dr2 == baseline.Dr2 && registers.Dr3 == baseline.Dr3 && registers.Dr7 == baseline.Dr7) {
                    marker("restored.txt");
                }
            });
            verifier.join();
            if (network) {
                SOCKET release = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
                sendto(release, "x", 1, 0, reinterpret_cast<sockaddr*>(&blockedAddress), sizeof(blockedAddress));
                closesocket(release);
            }
            checked = true;
        }
        Sleep(20);
    }
    if (blockedWorker.joinable()) blockedWorker.join();
    DestroyWindow(window);
    if (listener != INVALID_SOCKET) closesocket(listener);
    if (blockedReceiver != INVALID_SOCKET) closesocket(blockedReceiver);
    closesocket(sender);
    closesocket(receiver);
    WSACleanup();
    return 0;
}

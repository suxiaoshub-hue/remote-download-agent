#include <winsock2.h>
#include <windows.h>
#include <fstream>
#include <string>
#include <thread>

bool exists(const char* path) {
    return GetFileAttributesA(path) != INVALID_FILE_ATTRIBUTES;
}

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

int main() {
    WSADATA winsock{};
    if (WSAStartup(MAKEWORD(2, 2), &winsock)) return 1;
    SOCKET receiver = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    SOCKET sender = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    sockaddr_in address{};
    address.sin_family = AF_INET;
    address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    if (bind(receiver, reinterpret_cast<sockaddr*>(&address), sizeof(address))) return 2;
    int addressSize = sizeof(address);
    getsockname(receiver, reinterpret_cast<sockaddr*>(&address), &addressSize);
    if (connect(sender, reinterpret_cast<sockaddr*>(&address), sizeof(address))) return 3;
    WNDCLASSW windowClass{};
    windowClass.lpfnWndProc = windowProc;
    windowClass.hInstance = GetModuleHandleW(nullptr);
    windowClass.lpszClassName = L"CaptureFixture";
    RegisterClassW(&windowClass);
    HWND window = CreateWindowW(windowClass.lpszClassName, L"Fixture", 0, 0, 0, 0, 0,
                                nullptr, nullptr, windowClass.hInstance, nullptr);
    if (!window) return 4;
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
            produceCalls(window, sender);
            std::thread worker([sender] {
                const char data[] = "PCSTORY_NEW_THREAD_TEST";
                send(sender, data, sizeof(data), 0);
            });
            worker.join();
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
                if (read && !debugged && registers.Dr0 == 0 && registers.Dr1 == 0 &&
                    registers.Dr2 == 0 && registers.Dr3 == 0 && (registers.Dr7 & 0xff) == 0) {
                    marker("restored.txt");
                }
            });
            verifier.join();
            checked = true;
        }
        Sleep(20);
    }
    DestroyWindow(window);
    closesocket(sender);
    closesocket(receiver);
    WSACleanup();
    return 0;
}

# PcstoryDebugger

Standalone Windows x64 diagnostic tool. It attaches to an existing `pcstory.exe` and records `SendMessageW`, `PostMessageW`, `send`, and `WSASend` with hardware execution breakpoints. It resolves exports in the target process and captures register arguments, caller addresses, WM_COPYDATA payloads and bounded socket buffers. It does not infer or replay download commands.

Run as administrator after detaching x64dbg. Trigger one download manually and press Ctrl+C, or wait 90 seconds. Output is `pcstory-debug.txt` beside the executable. The tool pauses before closing, including on errors.

```
PcstoryDebugger.exe --pid 9888 --seconds 90 --output capture.txt
```

The PID is an example. Only x64 targets are supported. Existing debuggers and occupied hardware breakpoint slots are rejected. Debug registers are saved per thread and restored before detach. `DebugSetProcessKillOnExit(FALSE)` prevents debugger exit from terminating the target; use Ctrl+C for orderly cleanup rather than force termination. Captures can slow the target, and logs may contain outgoing application data.

Build with MSVC and CMake:

```
cmake -S debugger -B build/debugger -A x64
cmake --build build/debugger --config Release
pwsh -File debugger/tests/smoke.ps1 -Bin build/debugger/Release
```

The Windows workflow tests known messages, socket bytes, new-thread capture, restored debug registers, and a surviving target using a synthetic fixture. The static runtime removes the Python and VC redistributable prerequisites. Windows 7 runtime compatibility remains unverified.

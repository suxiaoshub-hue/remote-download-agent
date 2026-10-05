# Windows API capture plan

The user approved a standalone Windows x64 executable that records PCStory API calls to a local TXT file. No Python, server, paid extension, or injected DLL is required.

## Design

Use the Windows debugging API and four hardware execution breakpoints for `user32!SendMessageW`, `user32!PostMessageW`, `ws2_32!send`, and `ws2_32!WSASend`. Resolve exports in the target process so ASLR is respected. Capture the x64 argument registers, return address, bounded send buffers, and WM_COPYDATA data. Do not infer message semantics from numeric values alone.

Record the original debug registers of every thread. Restore them before detaching; disable process termination on debugger exit. Stop on Ctrl+C or after 90 seconds. Reject 32-bit targets, an existing debugger, and occupied hardware breakpoint slots. Log beside the executable and pause on interactive errors.

## Implementation and verification

- [x] Create a native Windows fixture that produces known window messages and socket payloads, including a thread created after debugger attachment.
- [x] Add a Windows Actions job and PowerShell smoke test. Observe the test fail because the debugger is absent.
- [x] Implement process discovery, export resolution, capture, and cleanup.
- [x] Build with MSVC x64 and a static runtime; verify all four APIs, captured bytes, Ctrl+C/timeout detach, original registers, and surviving fixture.
- [x] Package the EXE with Chinese instructions, push and build, download the verified artifact.

Windows 7 compatibility is a build target, not a verified operating-system result. The fixture proves API capture; only a PCStory test can establish its actual download interface.

## Verification evidence

Windows Actions run `37327311955` passed compile, normal capture/detach, Ctrl+C cleanup, and injected capture/restoration failure recovery for code commit `74ff9562927307a9d16ad39cb6620c1dfc4933a7`. Assertions require real per-API records and payload bytes, newly created thread capture, rejection of a second debugger, exact observed baseline debug-register values after detach, and a surviving target that exits normally. Windows reports zero for the disabled Dr0 seed, so the test compares the observed baseline instead of assuming the seed persists.

The downloaded `PcstoryDebugger-Windows-x64.zip` contains a 326144-byte EXE and Chinese instructions. ZIP SHA-256: `b38df1e69fd70ce1db5f3b3cc06b258c2f325cab047975ece105035b9a7ad8ed`.

Next validation requires running this tool alongside the user's PCStory installation and reviewing the generated TXT. No real PCStory download command has yet been established or replayed.

# Windows API capture plan

The user approved a standalone Windows x64 executable that records PCStory API calls to a local TXT file. No Python, server, paid extension, or injected DLL is required.

## Design

Use the Windows debugging API and four hardware execution breakpoints for `user32!SendMessageW`, `user32!PostMessageW`, `ws2_32!send`, and `ws2_32!WSASend`. Resolve exports in the target process so ASLR is respected. Capture the x64 argument registers, return address, bounded send buffers, and WM_COPYDATA data. Do not infer message semantics from numeric values alone.

Record the original debug registers of every thread. Restore them before detaching; disable process termination on debugger exit. Stop on Ctrl+C or after 90 seconds. Reject 32-bit targets, an existing debugger, and occupied hardware breakpoint slots. Log beside the executable and pause on interactive errors.

## Implementation and verification

- [ ] Create a native Windows fixture that produces known window messages and socket payloads, including a thread created after debugger attachment.
- [ ] Add a Windows Actions job and PowerShell smoke test. Observe the test fail because the debugger is absent.
- [ ] Implement process discovery, export resolution, capture, and cleanup.
- [ ] Build with MSVC x64 and a static runtime; verify all four APIs, captured bytes, Ctrl+C/timeout detach, original registers, and surviving fixture.
- [ ] Package the EXE with Chinese instructions, push and build, download the verified artifact.

Windows 7 compatibility is a build target, not a verified operating-system result. The fixture proves API capture; only a PCStory test can establish its actual download interface.

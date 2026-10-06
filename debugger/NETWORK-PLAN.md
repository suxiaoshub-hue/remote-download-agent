# Network return capture

The approved next step is to observe connect/send/recv results, actual synchronous receive bytes, and local TCP port owners. Keep three entry hardware breakpoints and reserve slot four for a per-thread return breakpoint. Save API arguments at entry and match the return address and stack pointer on return. Bound nesting to 16 and captures to 256 bytes. Restore the original debug registers on all exit paths. WSARecv asynchronous completion is outside this mode; do not label entry buffers as replies.

Record TCP rows owned by the target and local listeners on ports 12000 and 12200, including PID and executable path, at attachment and detach. Port ownership is a snapshot and does not prove command semantics. Provide a CMD launcher for network mode.

Validate successful connect/send, actual known receive payload, failed receive without buffer output, preserved debug registers and surviving fixture. Run the existing timeout, Ctrl+C and injected cleanup-failure tests too. Build and deliver the verified GitHub artifact.

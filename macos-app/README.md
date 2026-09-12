# macOS native app boundary

The future SwiftUI app is a client and policy surface, not an agent core. It
will communicate with the JL runtime through a versioned local IPC contract and
will own macOS consent UX, approval sheets, status, and native lifecycle.

Phase 1 intentionally contains no Swift or SwiftUI implementation.

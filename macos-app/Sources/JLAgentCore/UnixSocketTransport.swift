import Darwin
import Foundation

public protocol IPCTransport: Sendable {
  func send(
    endpoint: URL,
    data: Data,
    timeout: TimeInterval,
    maximumResponseBytes: Int
  ) throws -> Data
}

public struct UnixSocketTransport: IPCTransport {
  public init() {}

  public func send(
    endpoint: URL,
    data: Data,
    timeout: TimeInterval,
    maximumResponseBytes: Int
  ) throws -> Data {
    guard endpoint.isFileURL, timeout > 0, maximumResponseBytes > 0 else {
      throw RuntimeClientError.transport("Invalid local IPC configuration")
    }
    let path = endpoint.path
    let pathBytes = Array(path.utf8CString)
    guard pathBytes.count <= MemoryLayout.size(ofValue: sockaddr_un().sun_path) else {
      throw RuntimeClientError.transport("Runtime socket path is too long")
    }

    let descriptor = Darwin.socket(AF_UNIX, SOCK_STREAM, 0)
    guard descriptor >= 0 else {
      throw RuntimeClientError.transport("Unable to create local socket")
    }
    defer { Darwin.close(descriptor) }

    let flags = fcntl(descriptor, F_GETFL, 0)
    guard flags >= 0, fcntl(descriptor, F_SETFL, flags | O_NONBLOCK) == 0 else {
      throw RuntimeClientError.transport("Unable to configure local socket")
    }

    var address = sockaddr_un()
    address.sun_family = sa_family_t(AF_UNIX)
    let addressLength = MemoryLayout<sa_family_t>.size + pathBytes.count
    address.sun_len = UInt8(addressLength)
    withUnsafeMutablePointer(to: &address.sun_path) { destination in
      pathBytes.withUnsafeBytes { source in
        _ = memcpy(
          UnsafeMutableRawPointer(destination),
          source.baseAddress!,
          pathBytes.count
        )
      }
    }

    let connected = withUnsafePointer(to: &address) { pointer in
      pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) {
        Darwin.connect(descriptor, $0, socklen_t(addressLength))
      }
    }
    if connected != 0 {
      guard errno == EINPROGRESS else {
        throw RuntimeClientError.transport("Runtime is unavailable")
      }
      try wait(descriptor, events: Int16(POLLOUT), timeout: timeout)
      var socketError: Int32 = 0
      var errorLength = socklen_t(MemoryLayout<Int32>.size)
      guard
        getsockopt(
          descriptor,
          SOL_SOCKET,
          SO_ERROR,
          &socketError,
          &errorLength
        ) == 0, socketError == 0
      else {
        throw RuntimeClientError.transport("Runtime connection failed")
      }
    }

    let deadline = Date().addingTimeInterval(timeout)
    try writeAll(descriptor, data: data, deadline: deadline)
    return try readFrame(
      descriptor,
      maximum: maximumResponseBytes,
      deadline: deadline
    )
  }

  private func writeAll(
    _ descriptor: Int32,
    data: Data,
    deadline: Date
  ) throws {
    var offset = 0
    while offset < data.count {
      try wait(
        descriptor,
        events: Int16(POLLOUT),
        timeout: remaining(deadline)
      )
      let written = data.withUnsafeBytes { bytes in
        Darwin.send(
          descriptor,
          bytes.baseAddress!.advanced(by: offset),
          data.count - offset,
          0
        )
      }
      if written < 0, errno == EAGAIN || errno == EINTR { continue }
      guard written > 0 else {
        throw RuntimeClientError.transport("Runtime request could not be sent")
      }
      offset += written
    }
  }

  private func readFrame(
    _ descriptor: Int32,
    maximum: Int,
    deadline: Date
  ) throws -> Data {
    var received = Data()
    var buffer = [UInt8](repeating: 0, count: 4096)
    while received.count <= maximum {
      try wait(
        descriptor,
        events: Int16(POLLIN),
        timeout: remaining(deadline)
      )
      let count = Darwin.recv(descriptor, &buffer, buffer.count, 0)
      if count < 0, errno == EAGAIN || errno == EINTR { continue }
      if count == 0 { break }
      guard count > 0 else {
        throw RuntimeClientError.transport("Runtime response failed")
      }
      received.append(buffer, count: count)
      if let newline = received.firstIndex(of: 0x0A) {
        guard newline == received.index(before: received.endIndex) else {
          throw RuntimeClientError.malformedResponse
        }
        received.removeSubrange(newline..<received.endIndex)
        break
      }
    }
    guard !received.isEmpty else { throw RuntimeClientError.malformedResponse }
    guard received.count <= maximum else {
      throw RuntimeClientError.responseTooLarge
    }
    return received
  }

  private func wait(
    _ descriptor: Int32,
    events: Int16,
    timeout: TimeInterval
  ) throws {
    guard timeout > 0 else { throw RuntimeClientError.timeout }
    var item = pollfd(fd: descriptor, events: events, revents: 0)
    let milliseconds = Int32(min(timeout * 1_000, Double(Int32.max)))
    let result = Darwin.poll(&item, 1, milliseconds)
    if result == 0 { throw RuntimeClientError.timeout }
    if result < 0, errno == EINTR { return }
    let failures = Int16(POLLERR | POLLHUP | POLLNVAL)
    guard result > 0, item.revents & failures == 0 else {
      throw RuntimeClientError.transport("Runtime connection closed")
    }
  }

  private func remaining(_ deadline: Date) -> TimeInterval {
    max(0, deadline.timeIntervalSinceNow)
  }
}

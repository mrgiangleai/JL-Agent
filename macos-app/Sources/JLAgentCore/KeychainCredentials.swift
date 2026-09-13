import Darwin
import Foundation
import Security

public protocol CredentialProviding: Sendable {
  func loadCredential() throws -> String
}

public final class KeychainCredentialProvider: CredentialProviding, @unchecked Sendable {
  private let service: String
  private let account: String

  public init(
    service: String = "com.jlagent.runtime.ipc",
    account: String = "native-control-client"
  ) {
    self.service = service
    self.account = account
  }

  public func loadCredential() throws -> String {
    var item: CFTypeRef?
    var query = baseQuery
    query[kSecReturnData as String] = true
    query[kSecMatchLimit as String] = kSecMatchLimitOne
    let status = SecItemCopyMatching(query as CFDictionary, &item)
    if status == errSecItemNotFound {
      throw RuntimeClientError.missingCredential
    }
    guard
      status == errSecSuccess,
      let data = item as? Data,
      let value = String(data: data, encoding: .utf8),
      Self.isValid(value)
    else {
      throw RuntimeClientError.credential("Keychain credential is unavailable")
    }
    return value
  }

  @discardableResult
  public func loadOrImport(from runtimeFile: URL) throws -> String {
    do {
      return try loadCredential()
    } catch RuntimeClientError.missingCredential {
      return try refresh(from: runtimeFile)
    }
  }

  @discardableResult
  public func refresh(from runtimeFile: URL) throws -> String {
    let value = try Self.readPrivateCredential(runtimeFile)
    try store(value)
    return value
  }

  public func remove() throws {
    let status = SecItemDelete(baseQuery as CFDictionary)
    guard status == errSecSuccess || status == errSecItemNotFound else {
      throw RuntimeClientError.credential("Keychain credential could not be reset")
    }
  }

  private var baseQuery: [String: Any] {
    [
      kSecClass as String: kSecClassGenericPassword,
      kSecAttrService as String: service,
      kSecAttrAccount as String: account,
    ]
  }

  private func store(_ value: String) throws {
    guard Self.isValid(value) else {
      throw RuntimeClientError.credential("Runtime credential is malformed")
    }
    let data = Data(value.utf8)
    let updateStatus = SecItemUpdate(
      baseQuery as CFDictionary,
      [kSecValueData as String: data] as CFDictionary
    )
    if updateStatus == errSecSuccess { return }
    guard updateStatus == errSecItemNotFound else {
      throw RuntimeClientError.credential("Keychain credential could not be updated")
    }
    var attributes = baseQuery
    attributes[kSecValueData as String] = data
    attributes[kSecAttrAccessible as String] =
      kSecAttrAccessibleWhenUnlockedThisDeviceOnly
    guard SecItemAdd(attributes as CFDictionary, nil) == errSecSuccess else {
      throw RuntimeClientError.credential("Keychain credential could not be stored")
    }
  }

  private static func readPrivateCredential(_ url: URL) throws -> String {
    var details = stat()
    guard url.isFileURL, lstat(url.path, &details) == 0 else {
      throw RuntimeClientError.missingCredential
    }
    let fileType = details.st_mode & mode_t(S_IFMT)
    let permissions = details.st_mode & 0o777
    guard
      fileType == mode_t(S_IFREG),
      details.st_uid == geteuid(),
      permissions & 0o077 == 0,
      permissions & 0o600 == 0o600,
      details.st_size <= 512
    else {
      throw RuntimeClientError.credential("Runtime credential file is not private")
    }
    let data = try Data(contentsOf: url, options: [.uncached])
    guard
      let value = String(data: data, encoding: .utf8),
      isValid(value)
    else {
      throw RuntimeClientError.credential("Runtime credential is malformed")
    }
    return value
  }

  private static func isValid(_ value: String) -> Bool {
    (32...512).contains(value.count)
      && !value.contains("\n")
      && !value.contains("\r")
  }
}

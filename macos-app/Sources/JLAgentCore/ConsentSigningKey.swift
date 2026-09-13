import Darwin
import Foundation
import Security

public final class ConsentSigningKey: @unchecked Sendable {
  private let applicationTag: Data

  public init(tag: String = "com.jlagent.native-consent.v1") {
    self.applicationTag = Data(tag.utf8)
  }

  @discardableResult
  public func provisionPublicKey(at url: URL) throws -> Data {
    let key = try loadOrCreate()
    guard
      let publicKey = SecKeyCopyPublicKey(key),
      let representation = SecKeyCopyExternalRepresentation(publicKey, nil)
        as Data?
    else {
      throw RuntimeClientError.credential("Consent public key is unavailable")
    }
    try writePublicKey(representation, to: url)
    return representation
  }

  public func rotate(publicKeyURL: URL) throws {
    try remove()
    _ = try provisionPublicKey(at: publicKeyURL)
  }

  public func sign(
    challenge: ConsentChallenge,
    decision: ConsentDecision
  ) throws -> String {
    let message = canonicalConsentMessage(challenge: challenge, decision: decision)
    let key = try loadOrCreate()
    var error: Unmanaged<CFError>?
    guard
      let signature = SecKeyCreateSignature(
        key,
        .rsaSignatureMessagePKCS1v15SHA256,
        message as CFData,
        &error
      ) as Data?
    else {
      throw RuntimeClientError.credential("Consent signature failed")
    }
    return signature.base64EncodedString()
  }

  public func remove() throws {
    let status = SecItemDelete(keyQuery as CFDictionary)
    guard status == errSecSuccess || status == errSecItemNotFound else {
      throw RuntimeClientError.credential("Consent key could not be reset")
    }
  }

  private var keyQuery: [String: Any] {
    [
      kSecClass as String: kSecClassKey,
      kSecAttrApplicationTag as String: applicationTag,
      kSecAttrKeyType as String: kSecAttrKeyTypeRSA,
    ]
  }

  private func loadOrCreate() throws -> SecKey {
    var item: CFTypeRef?
    var query = keyQuery
    query[kSecReturnRef as String] = true
    query[kSecMatchLimit as String] = kSecMatchLimitOne
    let status = SecItemCopyMatching(query as CFDictionary, &item)
    if status == errSecSuccess, let key = item as! SecKey? { return key }
    guard status == errSecItemNotFound else {
      throw RuntimeClientError.credential("Consent key is unavailable")
    }
    let attributes: [String: Any] = [
      kSecAttrKeyType as String: kSecAttrKeyTypeRSA,
      kSecAttrKeySizeInBits as String: 2048,
      kSecPrivateKeyAttrs as String: [
        kSecAttrIsPermanent as String: true,
        kSecAttrApplicationTag as String: applicationTag,
        kSecAttrAccessible as String:
          kSecAttrAccessibleWhenUnlockedThisDeviceOnly,
      ],
    ]
    var error: Unmanaged<CFError>?
    guard let key = SecKeyCreateRandomKey(attributes as CFDictionary, &error) else {
      throw RuntimeClientError.credential("Consent key could not be created")
    }
    return key
  }

  private func writePublicKey(_ data: Data, to url: URL) throws {
    let parent = url.deletingLastPathComponent()
    try FileManager.default.createDirectory(
      at: parent,
      withIntermediateDirectories: true,
      attributes: [.posixPermissions: 0o700]
    )
    var directory = stat()
    guard
      lstat(parent.path, &directory) == 0,
      directory.st_uid == geteuid(),
      directory.st_mode & mode_t(S_IFMT) == mode_t(S_IFDIR),
      directory.st_mode & 0o077 == 0
    else {
      throw RuntimeClientError.credential("Runtime directory is not private")
    }
    if FileManager.default.fileExists(atPath: url.path) {
      var existing = stat()
      guard
        lstat(url.path, &existing) == 0,
        existing.st_uid == geteuid(),
        existing.st_mode & mode_t(S_IFMT) == mode_t(S_IFREG)
      else {
        throw RuntimeClientError.credential("Consent public key path is unsafe")
      }
    }
    try data.write(to: url, options: .atomic)
    guard chmod(url.path, 0o600) == 0 else {
      throw RuntimeClientError.credential("Consent public key could not be secured")
    }
  }
}

public func canonicalConsentMessage(
  challenge: ConsentChallenge,
  decision: ConsentDecision
) -> Data {
  var message = Data("jl-agent-consent-v1\0".utf8)
  for field in [
    challenge.consentID,
    challenge.requestID,
    challenge.callerID,
    challenge.sessionID,
    decision.rawValue,
    challenge.nonce,
  ] {
    let encoded = Data(field.utf8)
    var length = UInt64(encoded.count).bigEndian
    withUnsafeBytes(of: &length) { message.append(contentsOf: $0) }
    message.append(encoded)
  }
  return message
}

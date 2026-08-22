import Foundation
import Security

enum KeychainStoreError: LocalizedError {
    case unexpectedData
    case unhandledStatus(OSStatus)

    var errorDescription: String? {
        switch self {
        case .unexpectedData:
            return "The secure credential store returned invalid data."
        case .unhandledStatus:
            return "The secure credential store could not complete the request."
        }
    }
}

/// Stores opaque credentials only. Pairing codes are deliberately never persisted.
final class KeychainStore {
    private let service = "com.adaptivetrainingcoach.healthsync"
    private let tokenAccount = "mobile-sync-token"
    private let installationIDAccount = "installation-id"

    func readAccessToken() throws -> String? {
        try readString(account: tokenAccount)
    }

    func saveAccessToken(_ token: String) throws {
        try saveString(token, account: tokenAccount)
    }

    func deleteAccessToken() throws {
        try delete(account: tokenAccount)
    }

    func installationID() throws -> UUID {
        if let existingID = try readString(account: installationIDAccount) {
            guard let installationID = UUID(uuidString: existingID) else {
                throw KeychainStoreError.unexpectedData
            }
            return installationID
        }

        let generatedID = UUID()
        try saveString(generatedID.uuidString.lowercased(), account: installationIDAccount)
        return generatedID
    }

    private func readString(account: String) throws -> String? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne
        ]

        var item: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &item)

        switch status {
        case errSecItemNotFound:
            return nil
        case errSecSuccess:
            guard let data = item as? Data,
                  let value = String(data: data, encoding: .utf8)
            else {
                throw KeychainStoreError.unexpectedData
            }
            return value
        default:
            throw KeychainStoreError.unhandledStatus(status)
        }
    }

    private func saveString(_ value: String, account: String) throws {
        let data = Data(value.utf8)
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account
        ]
        let attributes: [String: Any] = [
            kSecValueData as String: data,
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        ]

        let updateStatus = SecItemUpdate(query as CFDictionary, attributes as CFDictionary)
        if updateStatus == errSecItemNotFound {
            var addQuery = query
            for (key, value) in attributes {
                addQuery[key] = value
            }
            let addStatus = SecItemAdd(addQuery as CFDictionary, nil)
            guard addStatus == errSecSuccess else {
                throw KeychainStoreError.unhandledStatus(addStatus)
            }
        } else if updateStatus != errSecSuccess {
            throw KeychainStoreError.unhandledStatus(updateStatus)
        }
    }

    private func delete(account: String) throws {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account
        ]
        let status = SecItemDelete(query as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else {
            throw KeychainStoreError.unhandledStatus(status)
        }
    }
}

import Foundation

enum AppConfigurationError: LocalizedError {
    case missingAPIBaseURL
    case invalidAPIBaseURL

    var errorDescription: String? {
        switch self {
        case .missingAPIBaseURL:
            return "Set COACH_API_BASE_URL in Config/Developer.xcconfig before pairing."
        case .invalidAPIBaseURL:
            return "COACH_API_BASE_URL must be a valid HTTPS URL."
        }
    }
}

enum AppConfiguration {
    static func apiBaseURL(bundle: Bundle = .main) throws -> URL {
        guard let rawValue = bundle.object(forInfoDictionaryKey: "CoachAPIBaseURL") as? String else {
            throw AppConfigurationError.missingAPIBaseURL
        }

        let value = rawValue.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !value.isEmpty, !value.contains("$("), let url = URL(string: value),
              url.scheme?.lowercased() == "https", url.host != nil
        else {
            throw AppConfigurationError.invalidAPIBaseURL
        }

        return url
    }
}

import Foundation

enum APIClientError: LocalizedError {
    case invalidResponse
    case invalidHTTPStatus(Int)
    case invalidPairingResponse

    var errorDescription: String? {
        switch self {
        case .invalidResponse:
            return "The Coach service returned an unreadable response."
        case let .invalidHTTPStatus(statusCode):
            if statusCode == 401 {
                return "This iPhone is no longer connected. Pair it again from Telegram."
            }
            if statusCode == 409 {
                return "This workout is already being synchronized. Try again in a moment."
            }
            return "The Coach service could not process this request."
        case .invalidPairingResponse:
            return "The Coach service did not return a device credential."
        }
    }
}

struct APIClient {
    private let baseURL: URL
    private let session: URLSession
    private let encoder: JSONEncoder
    private let decoder: JSONDecoder

    init(baseURL: URL, session: URLSession = .shared) {
        self.baseURL = baseURL
        self.session = session

        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        self.encoder = encoder

        self.decoder = JSONDecoder()
    }

    func pair(pairingCode: String, installationID: UUID) async throws -> String {
        let body = PairingRequest(pairingCode: pairingCode, installationID: installationID)
        let request = try makeJSONRequest(path: "v1/mobile/pair", body: body, bearerToken: nil)
        let data = try await execute(request)
        let response = try decoder.decode(PairingResponse.self, from: data)
        guard !response.accessToken.isEmpty else {
            throw APIClientError.invalidPairingResponse
        }
        return response.accessToken
    }

    func sync(workout: HealthKitWorkout, accessToken: String) async throws -> HealthKitWorkoutSyncResult {
        let body = HealthKitWorkoutSyncRequest(workouts: [workout.syncPayload])
        let request = try makeJSONRequest(
            path: "v1/mobile/healthkit/workouts:sync",
            body: body,
            bearerToken: accessToken
        )
        let data: Data
        do {
            data = try await execute(request)
        } catch APIClientError.invalidHTTPStatus(422) {
            // A user can update the iPhone app before the API deployment has
            // reached every server. Preserve basic workout sync in that window.
            let legacyBody = HealthKitLegacyWorkoutSyncRequest(
                workouts: [workout.legacySyncPayload]
            )
            let legacyRequest = try makeJSONRequest(
                path: "v1/mobile/healthkit/workouts:sync",
                body: legacyBody,
                bearerToken: accessToken
            )
            data = try await execute(legacyRequest)
        }
        let response = try decoder.decode(HealthKitWorkoutSyncResponse.self, from: data)
        guard let result = response.results.first(where: { $0.workoutUUID == workout.id }) else {
            throw APIClientError.invalidResponse
        }
        return result
    }

    private func makeJSONRequest<Body: Encodable>(
        path: String,
        body: Body,
        bearerToken: String?
    ) throws -> URLRequest {
        let url = baseURL.appendingPathComponent(path)
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if let bearerToken {
            request.setValue("Bearer \(bearerToken)", forHTTPHeaderField: "Authorization")
        }
        request.httpBody = try encoder.encode(body)
        return request
    }

    private func execute(_ request: URLRequest) async throws -> Data {
        let (data, response) = try await session.data(for: request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIClientError.invalidResponse
        }
        guard (200...299).contains(httpResponse.statusCode) else {
            throw APIClientError.invalidHTTPStatus(httpResponse.statusCode)
        }
        return data
    }
}

private struct PairingRequest: Encodable {
    let pairingCode: String
    let installationID: UUID

    enum CodingKeys: String, CodingKey {
        case pairingCode = "pairing_code"
        case installationID = "installation_id"
    }
}

private struct PairingResponse: Decodable {
    let accessToken: String

    enum CodingKeys: String, CodingKey {
        case accessToken = "access_token"
    }
}

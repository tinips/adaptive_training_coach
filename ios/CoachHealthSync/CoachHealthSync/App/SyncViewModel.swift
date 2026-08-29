import Combine
import Foundation

@MainActor
final class SyncViewModel: ObservableObject {
    @Published var pairingCode = ""
    @Published private(set) var isPaired = false
    @Published private(set) var workouts: [HealthKitWorkout] = []
    @Published var selectedWorkoutID: UUID?
    @Published private(set) var isWorking = false
    @Published private(set) var statusMessage: String?
    @Published private(set) var errorMessage: String?

    private let healthKitService: HealthKitServicing
    private let keychainStore: KeychainStore
    private var apiClient: APIClient?
    private var accessToken: String?
    private var installationID: UUID?

    init(
        healthKitService: HealthKitServicing = HealthKitService(),
        keychainStore: KeychainStore = KeychainStore()
    ) {
        self.healthKitService = healthKitService
        self.keychainStore = keychainStore
    }

    func prepare() {
        do {
            apiClient = APIClient(baseURL: try AppConfiguration.apiBaseURL())
            installationID = try keychainStore.installationID()
            accessToken = try keychainStore.readAccessToken()
            isPaired = accessToken != nil
        } catch {
            errorMessage = safeMessage(for: error)
        }
    }

    func pair() async {
        let normalizedCode = pairingCode.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
        guard !normalizedCode.isEmpty else {
            errorMessage = "Enter the pairing code shown by the Telegram bot."
            return
        }
        guard let apiClient, let installationID else {
            errorMessage = "Set the API URL in Developer.xcconfig, then reopen the app."
            return
        }

        await performWork {
            let token = try await apiClient.pair(pairingCode: normalizedCode, installationID: installationID)
            try keychainStore.saveAccessToken(token)
            accessToken = token
            isPaired = true
            pairingCode = ""
            statusMessage = "This iPhone is connected. Authorize Apple Health to continue."
        }
    }

    func resetConnection() {
        do {
            try keychainStore.deleteAccessToken()
            accessToken = nil
            isPaired = false
            workouts = []
            selectedWorkoutID = nil
            statusMessage = "Connection reset. Pair this iPhone from Telegram."
        } catch {
            errorMessage = safeMessage(for: error)
        }
    }

    func authorizeAndLoadWorkouts() async {
        await performWork {
            try await healthKitService.requestWorkoutReadAuthorization()
            statusMessage = "Apple Health access requested. Loading recent workouts..."
            try await loadWorkouts()
        }
    }

    func refreshWorkouts() async {
        await performWork {
            try await loadWorkouts()
        }
    }

    /// Syncs every recent workout without requiring a manual selection.
    ///
    /// Called on launch and whenever the app returns to the foreground. Each
    /// workout is synced individually through the same idempotent endpoint
    /// `syncSelectedWorkout()` uses, so re-running this after a partial
    /// failure or on an unchanged workout is always safe.
    func autoSyncOnLaunch() async {
        guard isPaired, let apiClient, let accessToken else {
            return
        }

        await performWork {
            try await healthKitService.requestWorkoutReadAuthorization()
            try await loadWorkouts()

            guard !workouts.isEmpty else {
                return
            }

            var insertedCount = 0
            var unchangedCount = 0
            var updatedCount = 0

            for workout in workouts {
                do {
                    let result = try await apiClient.sync(workout: workout, accessToken: accessToken)
                    switch result.outcome {
                    case .inserted: insertedCount += 1
                    case .unchanged: unchangedCount += 1
                    case .updated: updatedCount += 1
                    }
                } catch let APIClientError.invalidHTTPStatus(statusCode) where statusCode == 401 {
                    try? keychainStore.deleteAccessToken()
                    self.accessToken = nil
                    isPaired = false
                    throw APIClientError.invalidHTTPStatus(401)
                }
            }

            statusMessage = "Auto-synced: \(insertedCount) new, \(updatedCount) updated, \(unchangedCount) unchanged."
        }
    }

    func syncSelectedWorkout() async {
        guard let selectedWorkout = workouts.first(where: { $0.id == selectedWorkoutID }) else {
            errorMessage = "Select one workout to synchronize."
            return
        }
        guard let apiClient, let accessToken else {
            errorMessage = "Pair this iPhone from Telegram before synchronizing."
            return
        }

        await performWork {
            do {
                let result = try await apiClient.sync(workout: selectedWorkout, accessToken: accessToken)
                statusMessage = "Workout synchronized: \(result.outcome.rawValue)."
            } catch let APIClientError.invalidHTTPStatus(statusCode) where statusCode == 401 {
                try? keychainStore.deleteAccessToken()
                self.accessToken = nil
                isPaired = false
                throw APIClientError.invalidHTTPStatus(401)
            }
        }
    }

    func syncAllWorkouts() async {
        guard let apiClient, let accessToken else {
            errorMessage = "Pair this iPhone from Telegram before synchronizing."
            return
        }
        guard !workouts.isEmpty else {
            errorMessage = "Refresh workouts before synchronizing."
            return
        }

        await performWork {
            var insertedCount = 0
            var unchangedCount = 0
            var updatedCount = 0

            for workout in workouts {
                do {
                    let result = try await apiClient.sync(workout: workout, accessToken: accessToken)
                    switch result.outcome {
                    case .inserted: insertedCount += 1
                    case .unchanged: unchangedCount += 1
                    case .updated: updatedCount += 1
                    }
                } catch let APIClientError.invalidHTTPStatus(statusCode) where statusCode == 401 {
                    try? keychainStore.deleteAccessToken()
                    self.accessToken = nil
                    isPaired = false
                    throw APIClientError.invalidHTTPStatus(401)
                }
            }

            statusMessage = "Synced all: \(insertedCount) new, \(updatedCount) updated, \(unchangedCount) unchanged."
        }
    }

    func dismissError() {
        errorMessage = nil
    }

    private func loadWorkouts() async throws {
        let now = Date()
        guard let startDate = Calendar.current.date(byAdding: .month, value: -3, to: now) else {
            return
        }
        let loadedWorkouts = try await healthKitService.fetchWorkouts(from: startDate, to: now)
        workouts = loadedWorkouts
        if let selectedWorkoutID, !loadedWorkouts.contains(where: { $0.id == selectedWorkoutID }) {
            self.selectedWorkoutID = nil
        }
        if loadedWorkouts.isEmpty {
            statusMessage = "No workouts were found in the last 3 months."
        } else {
            statusMessage = "Choose one workout, then tap Sync now."
        }
    }

    private func performWork(_ operation: () async throws -> Void) async {
        isWorking = true
        errorMessage = nil
        defer { isWorking = false }

        do {
            try await operation()
        } catch {
            errorMessage = safeMessage(for: error)
        }
    }

    private func safeMessage(for error: Error) -> String {
        if let localizedError = error as? LocalizedError,
           let description = localizedError.errorDescription {
            return description
        }
        return "Something went wrong. Check the connection and try again."
    }
}

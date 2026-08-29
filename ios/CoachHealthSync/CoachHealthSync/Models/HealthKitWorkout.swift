import Foundation

/// A deliberately small representation of an `HKWorkout`.
///
/// HealthKit remains the source of the richer record on the phone. The POC sends
/// only the fields the Coach backend needs to normalize a training activity.
struct HealthKitWorkout: Identifiable, Equatable, Sendable {
    let id: UUID
    let activityType: String
    let activityDisplayName: String
    let startDate: Date
    let endDate: Date
    let durationSeconds: Double
    let distanceMeters: Double? = nil
    let elevationGainMeters: Double? = nil
    let elevationLossMeters: Double? = nil
    let caloriesKcal: Double? = nil
    let averageHeartRate: Double? = nil
    let maxHeartRate: Double? = nil
    let averageCadence: Double? = nil
    let maxCadence: Double? = nil
    let allStatistics: [String: HealthKitQuantityStatistics] = [:]
    let rawQuantitySamples: [HealthKitRawQuantitySample] = []
    let sourceName: String

    var syncPayload: HealthKitWorkoutSyncPayload {
        let normalizedDuration = Self.normalizedDurationSeconds(durationSeconds)
        return HealthKitWorkoutSyncPayload(
            workoutUUID: id,
            activityType: activityType,
            startedAt: startDate,
            endedAt: endDate,
            durationSeconds: normalizedDuration,
            movingDurationSeconds: normalizedDuration,
            distanceMeters: distanceMeters,
            elevationGainMeters: elevationGainMeters,
            elevationLossMeters: elevationLossMeters,
            caloriesKcal: caloriesKcal,
            averageHeartRate: averageHeartRate,
            maxHeartRate: maxHeartRate,
            averageCadence: averageCadence,
            maxCadence: maxCadence,
            sourceName: sourceName,
            allStatistics: allStatistics,
            rawQuantitySamples: rawQuantitySamples
        )
    }

    private static func normalizedDurationSeconds(_ duration: TimeInterval) -> Int {
        let rounded = duration.rounded()
        guard rounded.isFinite else {
            return 1
        }
        let maximumDurationSeconds = 7 * 24 * 60 * 60
        if rounded >= Double(maximumDurationSeconds) {
            return maximumDurationSeconds
        }
        return max(1, Int(rounded))
    }
}

/// Preserves every associated HealthKit statistic without forcing an unsafe
/// unit conversion for an identifier the app does not yet understand.
struct HealthKitQuantityStatistics: Encodable, Equatable, Sendable {
    let sum: String? = nil
    let minimum: String? = nil
    let maximum: String? = nil
    let average: String? = nil
}

/// A quantity measurement collected during the workout's time window from the
/// same HealthKit source as the workout. `value` is HealthKit's unit-qualified
/// representation and is retained verbatim for future normalization.
struct HealthKitRawQuantitySample: Encodable, Equatable, Sendable {
    let sampleUUID: UUID
    let quantityType: String
    let startedAt: Date
    let endedAt: Date
    let value: String
    let heartRateBPM: Double? = nil
    let sourceName: String? = nil
    let association: String

    enum CodingKeys: String, CodingKey {
        case sampleUUID = "sample_uuid"
        case quantityType = "quantity_type"
        case startedAt = "started_at"
        case endedAt = "ended_at"
        case value
        case heartRateBPM = "heart_rate_bpm"
        case sourceName = "source_name"
        case association
    }
}

/// The versioned boundary sent to the Coach backend.
///
/// `activity_type` carries a small HealthKit semantic key rather than a Coach
/// discipline. The backend owns the mapping from that key to its discipline
/// enum, so the iPhone app does not duplicate product rules.
struct HealthKitWorkoutSyncPayload: Encodable, Equatable, Sendable {
    let workoutUUID: UUID
    let activityType: String
    let startedAt: Date
    let endedAt: Date
    let durationSeconds: Int
    let movingDurationSeconds: Int? = nil
    let distanceMeters: Double? = nil
    let elevationGainMeters: Double? = nil
    let elevationLossMeters: Double? = nil
    let caloriesKcal: Double? = nil
    let averageHeartRate: Double? = nil
    let maxHeartRate: Double? = nil
    let averageCadence: Double? = nil
    let maxCadence: Double? = nil
    let sourceName: String? = nil
    let allStatistics: [String: HealthKitQuantityStatistics] = [:]
    let rawQuantitySamples: [HealthKitRawQuantitySample] = []

    enum CodingKeys: String, CodingKey {
        case workoutUUID = "workout_uuid"
        case activityType = "activity_type"
        case startedAt = "started_at"
        case endedAt = "ended_at"
        case durationSeconds = "duration_seconds"
        case movingDurationSeconds = "moving_duration_seconds"
        case distanceMeters = "distance_meters"
        case elevationGainMeters = "elevation_gain_meters"
        case elevationLossMeters = "elevation_loss_meters"
        case caloriesKcal = "calories_kcal"
        case averageHeartRate = "average_heart_rate"
        case maxHeartRate = "max_heart_rate"
        case averageCadence = "average_cadence"
        case maxCadence = "max_cadence"
        case sourceName = "source_name"
        case allStatistics = "all_statistics"
        case rawQuantitySamples = "raw_quantity_samples"
    }
}

struct HealthKitWorkoutSyncRequest: Encodable, Sendable {
    let workouts: [HealthKitWorkoutSyncPayload]
}

enum WorkoutSyncOutcome: String, Decodable, Sendable {
    case inserted
    case updated
    case unchanged
}

struct HealthKitWorkoutSyncResult: Decodable, Equatable, Sendable {
    let workoutUUID: UUID
    let outcome: WorkoutSyncOutcome

    enum CodingKeys: String, CodingKey {
        case workoutUUID = "workout_uuid"
        case outcome
    }
}

struct HealthKitWorkoutSyncResponse: Decodable, Sendable {
    let results: [HealthKitWorkoutSyncResult]
}

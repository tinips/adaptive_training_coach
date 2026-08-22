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
    let distanceMeters: Double?
    let caloriesKcal: Double?
    let sourceName: String

    var syncPayload: HealthKitWorkoutSyncPayload {
        HealthKitWorkoutSyncPayload(
            workoutUUID: id,
            activityType: activityType,
            startedAt: startDate,
            endedAt: endDate,
            durationSeconds: Self.normalizedDurationSeconds(durationSeconds),
            distanceMeters: distanceMeters,
            caloriesKcal: caloriesKcal
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
    let distanceMeters: Double?
    let caloriesKcal: Double?

    enum CodingKeys: String, CodingKey {
        case workoutUUID = "workout_uuid"
        case activityType = "activity_type"
        case startedAt = "started_at"
        case endedAt = "ended_at"
        case durationSeconds = "duration_seconds"
        case distanceMeters = "distance_meters"
        case caloriesKcal = "calories_kcal"
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

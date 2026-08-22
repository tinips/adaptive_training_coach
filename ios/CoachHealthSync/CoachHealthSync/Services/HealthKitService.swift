import Foundation
import HealthKit

protocol HealthKitServicing {
    func requestWorkoutReadAuthorization() async throws
    func fetchWorkouts(from startDate: Date, to endDate: Date) async throws -> [HealthKitWorkout]
}

enum HealthKitServiceError: LocalizedError {
    case authorizationRequestDidNotComplete
    case queryReturnedUnexpectedObjects

    var errorDescription: String? {
        switch self {
        case .authorizationRequestDidNotComplete:
            return "Apple Health authorization did not complete."
        case .queryReturnedUnexpectedObjects:
            return "Apple Health returned an unexpected workout record."
        }
    }
}

final class HealthKitService: HealthKitServicing {
    private let healthStore: HKHealthStore

    init(healthStore: HKHealthStore = HKHealthStore()) {
        self.healthStore = healthStore
    }

    func requestWorkoutReadAuthorization() async throws {
        let workoutType = HKObjectType.workoutType()

        try await withCheckedThrowingContinuation { continuation in
            healthStore.requestAuthorization(toShare: [], read: [workoutType]) { success, error in
                if let error {
                    continuation.resume(throwing: error)
                } else if success {
                    continuation.resume()
                } else {
                    continuation.resume(throwing: HealthKitServiceError.authorizationRequestDidNotComplete)
                }
            }
        }
    }

    func fetchWorkouts(from startDate: Date, to endDate: Date) async throws -> [HealthKitWorkout] {
        let workoutType = HKObjectType.workoutType()
        let predicate = HKQuery.predicateForSamples(
            withStart: startDate,
            end: endDate,
            options: [.strictStartDate]
        )
        let sortDescriptors = [NSSortDescriptor(key: HKSampleSortIdentifierStartDate, ascending: false)]

        let workouts: [HKWorkout] = try await withCheckedThrowingContinuation { continuation in
            let query = HKSampleQuery(
                sampleType: workoutType,
                predicate: predicate,
                limit: HKObjectQueryNoLimit,
                sortDescriptors: sortDescriptors
            ) { _, samples, error in
                if let error {
                    continuation.resume(throwing: error)
                    return
                }

                guard let samples else {
                    continuation.resume(returning: [])
                    return
                }

                guard let workouts = samples as? [HKWorkout] else {
                    continuation.resume(throwing: HealthKitServiceError.queryReturnedUnexpectedObjects)
                    return
                }

                continuation.resume(returning: workouts)
            }
            healthStore.execute(query)
        }

        return workouts.map(Self.map)
    }

    private static func map(_ workout: HKWorkout) -> HealthKitWorkout {
        let activityTypeRawValue = Int(workout.workoutActivityType.rawValue)
        return HealthKitWorkout(
            id: workout.uuid,
            activityType: HealthKitActivityType.syncKey(rawValue: activityTypeRawValue),
            activityDisplayName: HealthKitActivityType.displayName(rawValue: activityTypeRawValue),
            startDate: workout.startDate,
            endDate: workout.endDate,
            durationSeconds: workout.duration,
            distanceMeters: quantityValue(
                for: workout,
                quantityType: distanceQuantityType(for: workout.workoutActivityType),
                unit: .meter()
            ),
            caloriesKcal: quantityValue(
                for: workout,
                quantityType: HKQuantityType.quantityType(forIdentifier: .activeEnergyBurned),
                unit: .kilocalorie()
            ),
            sourceName: workout.sourceRevision.source.name
        )
    }

    private static func distanceQuantityType(for activityType: HKWorkoutActivityType) -> HKQuantityType? {
        switch activityType {
        case .running, .walking, .hiking:
            return HKQuantityType.quantityType(forIdentifier: .distanceWalkingRunning)
        case .cycling:
            return HKQuantityType.quantityType(forIdentifier: .distanceCycling)
        case .swimming:
            return HKQuantityType.quantityType(forIdentifier: .distanceSwimming)
        default:
            return nil
        }
    }

    private static func quantityValue(
        for workout: HKWorkout,
        quantityType: HKQuantityType?,
        unit: HKUnit
    ) -> Double? {
        guard let quantityType,
              let quantity = workout.statistics(for: quantityType)?.sumQuantity()
        else {
            return nil
        }

        return quantity.doubleValue(for: unit)
    }
}

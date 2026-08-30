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
        let readTypes = Set<HKObjectType>([workoutType]).union(
            Self.workoutQuantityTypes.map { $0 as HKObjectType }
        )

        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            healthStore.requestAuthorization(toShare: [], read: readTypes) { success, error in
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

        let rawQuantitySamples = await fetchRawQuantitySamples(
            from: startDate,
            to: endDate
        )
        let samplesByWorkout = Self.assignSamplesExclusively(
            samples: rawQuantitySamples,
            workouts: workouts
        )
        return workouts.map { workout in
            Self.map(workout, rawQuantitySamples: samplesByWorkout[workout.uuid] ?? [])
        }
    }

    /// Assigns each raw quantity sample to at most one workout.
    ///
    /// HealthKit does not expose which workout a background quantity sample
    /// (heart rate, steps, calories, ...) "belongs" to; the app approximates
    /// this with a same-source, overlapping-time-window heuristic. Some
    /// sources (observed with "Mi Fitness") log multiple `HKWorkout` entries
    /// whose time ranges overlap, so the naive heuristic can otherwise
    /// attach the exact same sample to more than one workout. The backend
    /// stores each physical sample once ever, so a duplicate attachment is
    /// rejected as a conflict instead of silently double-counting one
    /// reading as if it happened twice. Assigning each sample to only the
    /// single workout it overlaps most keeps every sample represented
    /// exactly once, attributed to its best match.
    private static func assignSamplesExclusively(
        samples: [HKQuantitySample],
        workouts: [HKWorkout]
    ) -> [UUID: [HKQuantitySample]] {
        var grouped: [UUID: [HKQuantitySample]] = [:]

        for sample in samples {
            var bestWorkout: HKWorkout?
            var bestOverlap: TimeInterval = 0

            for workout in workouts {
                guard sample.sourceRevision.source.bundleIdentifier
                    == workout.sourceRevision.source.bundleIdentifier,
                    sample.startDate < workout.endDate,
                    sample.endDate > workout.startDate
                else {
                    continue
                }

                let overlapStart = max(sample.startDate, workout.startDate)
                let overlapEnd = min(sample.endDate, workout.endDate)
                let overlap = overlapEnd.timeIntervalSince(overlapStart)
                guard overlap > 0 else {
                    continue
                }

                if overlap > bestOverlap
                    || (overlap == bestOverlap
                        && (bestWorkout.map { workout.startDate < $0.startDate } ?? true))
                {
                    bestWorkout = workout
                    bestOverlap = overlap
                }
            }

            if let bestWorkout {
                grouped[bestWorkout.uuid, default: []].append(sample)
            }
        }

        return grouped
    }

    private static func map(
        _ workout: HKWorkout,
        rawQuantitySamples: [HKQuantitySample]
    ) -> HealthKitWorkout {
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
            elevationGainMeters: quantityValue(
                for: workout,
                quantityType: quantityType(
                    forIdentifier: "HKQuantityTypeIdentifierElevationAscended"
                ),
                unit: .meter()
            ),
            elevationLossMeters: quantityValue(
                for: workout,
                quantityType: quantityType(
                    forIdentifier: "HKQuantityTypeIdentifierElevationDescended"
                ),
                unit: .meter()
            ),
            caloriesKcal: quantityValue(
                for: workout,
                quantityType: HKQuantityType.quantityType(forIdentifier: .activeEnergyBurned),
                unit: .kilocalorie()
            ),
            averageHeartRate: averageQuantityValue(
                for: workout,
                quantityType: HKQuantityType.quantityType(forIdentifier: .heartRate),
                unit: HKUnit.count().unitDivided(by: .minute())
            ),
            maxHeartRate: maximumQuantityValue(
                for: workout,
                quantityType: HKQuantityType.quantityType(forIdentifier: .heartRate),
                unit: HKUnit.count().unitDivided(by: .minute())
            ),
            averageCadence: averageCadence(for: workout),
            maxCadence: maximumQuantityValue(
                for: workout,
                quantityType: cadenceQuantityType(for: workout.workoutActivityType),
                unit: HKUnit.count().unitDivided(by: .minute())
            ),
            sourceName: workout.sourceRevision.source.name,
            allStatistics: allStatistics(for: workout),
            rawQuantitySamples: rawSamples(from: rawQuantitySamples)
        )
    }

    private static let workoutQuantityTypeIdentifiers = [
        "HKQuantityTypeIdentifierActiveEnergyBurned",
        "HKQuantityTypeIdentifierBasalEnergyBurned",
        "HKQuantityTypeIdentifierHeartRate",
        "HKQuantityTypeIdentifierHeartRateVariabilitySDNN",
        "HKQuantityTypeIdentifierRestingHeartRate",
        "HKQuantityTypeIdentifierWalkingHeartRateAverage",
        "HKQuantityTypeIdentifierStepCount",
        "HKQuantityTypeIdentifierDistanceWalkingRunning",
        "HKQuantityTypeIdentifierDistanceCycling",
        "HKQuantityTypeIdentifierDistanceSwimming",
        "HKQuantityTypeIdentifierFlightsClimbed",
        "HKQuantityTypeIdentifierElevationAscended",
        "HKQuantityTypeIdentifierElevationDescended",
        "HKQuantityTypeIdentifierCyclingCadence",
        "HKQuantityTypeIdentifierCyclingSpeed",
        "HKQuantityTypeIdentifierCyclingPower",
        "HKQuantityTypeIdentifierCyclingFunctionalThresholdPower",
        "HKQuantityTypeIdentifierRunningSpeed",
        "HKQuantityTypeIdentifierRunningPower",
        "HKQuantityTypeIdentifierRunningGroundContactTime",
        "HKQuantityTypeIdentifierRunningStrideLength",
        "HKQuantityTypeIdentifierRunningVerticalOscillation",
        "HKQuantityTypeIdentifierSwimmingStrokeCount",
        "HKQuantityTypeIdentifierVO2Max",
    ]

    private static let workoutQuantityTypes: Set<HKQuantityType> = Set(
        workoutQuantityTypeIdentifiers.compactMap { identifier in
            quantityType(forIdentifier: identifier)
        }
    )

    private static func quantityType(forIdentifier identifier: String) -> HKQuantityType? {
        HKQuantityType.quantityType(forIdentifier: HKQuantityTypeIdentifier(rawValue: identifier))
    }

    private func fetchRawQuantitySamples(
        from startDate: Date,
        to endDate: Date
    ) async -> [HKQuantitySample] {
        let store = healthStore
        return await withTaskGroup(of: [HKQuantitySample].self) { group in
            for quantityType in Self.workoutQuantityTypes {
                group.addTask { [store] in
                    do {
                        return try await Self.fetchQuantitySamples(
                            from: store,
                            quantityType: quantityType,
                            startDate: startDate,
                            endDate: endDate
                        )
                    } catch {
                        // A provider can deny or omit one metric while still
                        // exposing a valid workout. Metrics are best-effort;
                        // never hide the user's complete workout list.
                        return []
                    }
                }
            }

            var samples: [HKQuantitySample] = []
            for await quantitySamples in group {
                samples.append(contentsOf: quantitySamples)
            }
            return samples
        }
    }

    private static func fetchQuantitySamples(
        from healthStore: HKHealthStore,
        quantityType: HKQuantityType,
        startDate: Date,
        endDate: Date
    ) async throws -> [HKQuantitySample] {
        let predicate = HKQuery.predicateForSamples(
            withStart: startDate,
            end: endDate,
            options: []
        )
        return try await withCheckedThrowingContinuation { continuation in
            let query = HKSampleQuery(
                sampleType: quantityType,
                predicate: predicate,
                limit: HKObjectQueryNoLimit,
                sortDescriptors: nil
            ) { _, samples, error in
                if let error {
                    continuation.resume(throwing: error)
                    return
                }
                continuation.resume(
                    returning: (samples ?? []).compactMap { $0 as? HKQuantitySample }
                )
            }
            healthStore.execute(query)
        }
    }

    private static func allStatistics(
        for workout: HKWorkout
    ) -> [String: HealthKitQuantityStatistics] {
        Dictionary(
            uniqueKeysWithValues: workout.allStatistics.compactMap {
                quantityType,
                statistics in
                let values = HealthKitQuantityStatistics(
                    sum: statistics.sumQuantity()?.description,
                    minimum: statistics.minimumQuantity()?.description,
                    maximum: statistics.maximumQuantity()?.description,
                    average: statistics.averageQuantity()?.description
                )
                guard values.sum != nil || values.minimum != nil ||
                    values.maximum != nil || values.average != nil
                else {
                    return nil
                }
                return (quantityType.identifier, values)
            }
        )
    }

    /// Formats the samples already exclusively assigned to this workout by
    /// `assignSamplesExclusively`. No further time-window or source
    /// filtering happens here.
    private static func rawSamples(
        from samples: [HKQuantitySample]
    ) -> [HealthKitRawQuantitySample] {
        samples.map { sample in
            HealthKitRawQuantitySample(
                sampleUUID: sample.uuid,
                quantityType: sample.quantityType.identifier,
                startedAt: sample.startDate,
                endedAt: sample.endDate,
                value: sample.quantity.description,
                heartRateBPM: heartRateBPM(for: sample),
                sourceName: sample.sourceRevision.source.name,
                association: "time_window_source_match"
            )
        }
    }

    private static func heartRateBPM(for sample: HKQuantitySample) -> Double? {
        guard sample.quantityType == HKQuantityType.quantityType(forIdentifier: .heartRate)
        else {
            return nil
        }
        return sample.quantity.doubleValue(
            for: HKUnit.count().unitDivided(by: .minute())
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

    private static func averageQuantityValue(
        for workout: HKWorkout,
        quantityType: HKQuantityType?,
        unit: HKUnit
    ) -> Double? {
        guard let quantityType,
              let quantity = workout.statistics(for: quantityType)?.averageQuantity()
        else { return nil }
        return quantity.doubleValue(for: unit)
    }

    private static func maximumQuantityValue(
        for workout: HKWorkout,
        quantityType: HKQuantityType?,
        unit: HKUnit
    ) -> Double? {
        guard let quantityType,
              let quantity = workout.statistics(for: quantityType)?.maximumQuantity()
        else { return nil }
        return quantity.doubleValue(for: unit)
    }

    private static func averageCadence(for workout: HKWorkout) -> Double? {
        let cadenceUnit = HKUnit.count().unitDivided(by: .minute())
        if let directCadence = averageQuantityValue(
            for: workout,
            quantityType: cadenceQuantityType(for: workout.workoutActivityType),
            unit: cadenceUnit
        ) {
            return directCadence
        }
        guard workout.workoutActivityType == .running,
              let steps = quantityValue(
                  for: workout,
                  quantityType: HKQuantityType.quantityType(forIdentifier: .stepCount),
                  unit: .count()
              )
        else { return nil }
        return steps / workout.duration * 60
    }

    private static func cadenceQuantityType(
        for activityType: HKWorkoutActivityType
    ) -> HKQuantityType? {
        activityType == .cycling
            ? HKQuantityType.quantityType(forIdentifier: .cyclingCadence)
            : nil
    }
}

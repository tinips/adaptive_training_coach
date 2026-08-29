import HealthKit
import XCTest
@testable import CoachHealthSync

final class HealthKitActivityTypeTests: XCTestCase {
    func testKnownActivityTypesUseStableSemanticKeys() {
        XCTAssertEqual(
            HealthKitActivityType.syncKey(rawValue: Int(HKWorkoutActivityType.running.rawValue)),
            "running"
        )
        XCTAssertEqual(
            HealthKitActivityType.syncKey(rawValue: Int(HKWorkoutActivityType.functionalStrengthTraining.rawValue)),
            "functionalStrengthTraining"
        )
    }

    func testUnknownActivityTypeFallsBackToOther() {
        XCTAssertEqual(HealthKitActivityType.syncKey(rawValue: Int.max), "other")
    }

    func testSyncPayloadUsesBackendFieldNames() throws {
        let payload = HealthKitWorkoutSyncPayload(
            workoutUUID: UUID(uuidString: "A2C8D5D0-8ECA-43D9-AB7C-88EBE4B45EDC")!,
            activityType: "running",
            startedAt: Date(timeIntervalSince1970: 1_735_689_600),
            endedAt: Date(timeIntervalSince1970: 1_735_693_200),
            durationSeconds: 3_600,
            distanceMeters: 10_000,
            caloriesKcal: 720
        )
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        let encoded = try encoder.encode(payload)
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: encoded) as? [String: Any])

        XCTAssertEqual(
            (object["workout_uuid"] as? String)?.lowercased(),
            "a2c8d5d0-8eca-43d9-ab7c-88ebe4b45edc"
        )
        XCTAssertEqual(object["activity_type"] as? String, "running")
        XCTAssertEqual(object["duration_seconds"] as? Int, 3_600)
        XCTAssertEqual(object["distance_meters"] as? Double, 10_000)
        XCTAssertEqual(object["calories_kcal"] as? Double, 720)
        XCTAssertNil(object["source_name"])
    }

    func testWorkoutPayloadRoundsDurationToPositiveInteger() {
        let workout = HealthKitWorkout(
            id: UUID(),
            activityType: "running",
            activityDisplayName: "Running",
            startDate: Date(timeIntervalSince1970: 1_735_689_600),
            endDate: Date(timeIntervalSince1970: 1_735_689_646),
            durationSeconds: 45.6,
            distanceMeters: nil,
            caloriesKcal: nil,
            sourceName: "Mi Fitness"
        )

        XCTAssertEqual(workout.syncPayload.durationSeconds, 46)
    }

    func testLegacyPayloadDoesNotIncludeRichMetricFields() throws {
        let workout = HealthKitWorkout(
            id: UUID(),
            activityType: "running",
            activityDisplayName: "Running",
            startDate: Date(timeIntervalSince1970: 1_735_689_600),
            endDate: Date(timeIntervalSince1970: 1_735_693_200),
            durationSeconds: 3_600,
            sourceName: "Mi Fitness"
        )
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        let encoded = try encoder.encode(workout.legacySyncPayload)
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: encoded) as? [String: Any])

        XCTAssertNil(object["all_statistics"])
        XCTAssertNil(object["raw_quantity_samples"])
        XCTAssertNil(object["source_name"])
    }
}

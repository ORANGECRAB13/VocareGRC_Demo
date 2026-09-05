import Capacitor
import Foundation

@objc(TranslationHistoryPlugin)
public final class TranslationHistoryPlugin: CAPPlugin, CAPBridgedPlugin {
    public let identifier = "TranslationHistoryPlugin"
    public let jsName = "TranslationHistory"
    public let pluginMethods: [CAPPluginMethod] = [
        CAPPluginMethod(name: "saveSession", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "listSessions", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "getSession", returnType: CAPPluginReturnPromise)
    ]

    @objc func saveSession(_ call: CAPPluginCall) {
        guard let sessionId = call.getString("sessionId"), !sessionId.isEmpty else {
            call.reject("A sessionId is required.")
            return
        }
        let input = TranslationSessionInput(
            sessionId: sessionId,
            callerName: call.getString("callerName"), topic: call.getString("topic"),
            languageA: call.getString("languageA"), languageB: call.getString("languageB"),
            participantA: call.getString("participantA"), participantB: call.getString("participantB"),
            status: call.getString("status"), durationSeconds: call.getInt("durationSeconds"),
            transcriptJSON: call.getString("transcriptJSON"), startedAt: nil
        )
        Task { @MainActor in
            do {
                let record = try TranslationHistoryStore.shared.upsert(input)
                call.resolve(["session": summary(record)])
            } catch {
                call.reject("Could not save translation history.", nil, error)
            }
        }
    }

    @objc func listSessions(_ call: CAPPluginCall) {
        Task { @MainActor in
            do {
                let sessions = try TranslationHistoryStore.shared.list().map(summary)
                call.resolve(["sessions": sessions])
            } catch {
                call.reject("Could not load translation history.", nil, error)
            }
        }
    }

    @objc func getSession(_ call: CAPPluginCall) {
        guard let sessionId = call.getString("sessionId"), !sessionId.isEmpty else {
            call.reject("A sessionId is required.")
            return
        }
        Task { @MainActor in
            do {
                guard let record = try TranslationHistoryStore.shared.get(sessionId) else {
                    call.resolve(["session": NSNull()])
                    return
                }
                call.resolve(["session": detail(record)])
            } catch {
                call.reject("Could not load the translation session.", nil, error)
            }
        }
    }

    @MainActor
    private func summary(_ record: StoredTranslationSession) -> JSObject {
        [
            "session_id": record.sessionId,
            "caller_name": record.callerName,
            "topic": record.topic,
            "lang": record.languageA,
            "lang_a": record.languageA,
            "lang_b": record.languageB,
            "participant_count": 2,
            "status": record.status,
            "duration": record.durationSeconds,
            "started_at": ISO8601DateFormatter().string(from: record.startedAt),
            "updated_at": ISO8601DateFormatter().string(from: record.updatedAt)
        ]
    }

    @MainActor
    private func detail(_ record: StoredTranslationSession) -> JSObject {
        var value = summary(record)
        let data = Data(record.transcriptJSON.utf8)
        let transcript = (try? JSONSerialization.jsonObject(with: data) as? [Any]) ?? []
        value["transcript"] = transcript
        value["participant_a"] = record.participantA
        value["participant_b"] = record.participantB
        return value
    }
}

final class VocareBridgeViewController: CAPBridgeViewController {
    override func capacitorDidLoad() {
        super.capacitorDidLoad()
        bridge?.registerPluginInstance(TranslationHistoryPlugin())
        bridge?.registerPluginInstance(OfflineTranslatePlugin())
        bridge?.registerPluginInstance(PurchasesPlugin())
    }
}

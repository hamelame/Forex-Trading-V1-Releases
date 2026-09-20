import Foundation

struct APIClient {
    let baseURL = URL(string: "https://forex-trading-v1-mobile.onrender.com")!

    func request<T: Decodable>(_ path: String, token: String, method: String = "GET", body: Data? = nil) async throws -> T {
        let cleanPath = path.hasPrefix("/") ? String(path.dropFirst()) : path
        var lastError: Error?

        // Render's free service can be asleep. Give the first connection enough
        // time to wake the cloud engine and retry transient network failures.
        for attempt in 1...3 {
            do {
                var r = URLRequest(url: baseURL.appendingPathComponent(cleanPath))
                r.httpMethod = method
                r.httpBody = body
                r.timeoutInterval = 120
                r.cachePolicy = .reloadIgnoringLocalAndRemoteCacheData
                r.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
                r.setValue("application/json", forHTTPHeaderField: "Content-Type")

                let (data, res) = try await URLSession.shared.data(for: r)
                guard let h = res as? HTTPURLResponse else {
                    throw URLError(.badServerResponse)
                }
                if h.statusCode == 401 {
                    throw APIError.unauthorized
                }
                guard (200..<300).contains(h.statusCode) else {
                    throw APIError.http(h.statusCode)
                }
                do {
                    return try JSONDecoder().decode(T.self, from: data)
                } catch {
                    let raw = String(data: data, encoding: .utf8) ?? "<non-utf8>"
                    print("DECODE_ERROR:", error)
                    print("DECODE_RESPONSE_PREFIX:", String(raw.prefix(1200)))
                    throw error
                }
            } catch APIError.unauthorized {
                throw APIError.unauthorized
            } catch {
                lastError = error
                if attempt < 3 {
                    try? await Task.sleep(for: .seconds(3))
                }
            }
        }
        throw lastError ?? URLError(.cannotConnectToHost)
    }
}

enum APIError: LocalizedError {
    case unauthorized
    case http(Int)

    var errorDescription: String? {
        switch self {
        case .unauthorized: return "Wrong mobile access token"
        case .http(let code): return "Server error (HTTP \(code))"
        }
    }
}

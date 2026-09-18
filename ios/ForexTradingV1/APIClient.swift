import Foundation

struct APIClient {
    let baseURL = URL(string: "https://forex-trading-v1-mobile.onrender.com")!
    func request<T: Decodable>(_ path: String, token: String, method: String = "GET", body: Data? = nil) async throws -> T {
        var r = URLRequest(url: baseURL.appendingPathComponent(path))
        r.httpMethod = method; r.httpBody = body; r.timeoutInterval = 15
        r.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        r.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let (data,res)=try await URLSession.shared.data(for:r)
        guard let h=res as? HTTPURLResponse, (200..<300).contains(h.statusCode) else { throw URLError(.userAuthenticationRequired) }
        return try JSONDecoder().decode(T.self,from:data)
    }
}

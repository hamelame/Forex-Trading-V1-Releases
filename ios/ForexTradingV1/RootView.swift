import SwiftUI
import WebKit
import UIKit

/// Existing TestFlight app ID, now displaying the v2.9.2.6 PAPER mobile terminal.
/// The API and trading engine stay on Render; no access token is embedded here.
struct RootView: View {
    @State private var generation = 0
    @State private var connectionError: String?
    @State private var loading = true

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()

            MobileTerminalWebView(
                connectionError: $connectionError,
                loading: $loading
            )
            .id(generation)
            .ignoresSafeArea()

            if let connectionError {
                VStack(spacing: 14) {
                    Image(systemName: "wifi.exclamationmark")
                        .font(.system(size: 42))
                        .foregroundStyle(.yellow)
                    Text("Connection interrupted")
                        .font(.title3.bold())
                    Text(connectionError)
                        .font(.footnote)
                        .multilineTextAlignment(.center)
                        .foregroundStyle(.secondary)
                    Text("Free Render instances can take a minute to wake up.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                    Button("Try again") {
                        self.connectionError = nil
                        loading = true
                        generation += 1
                    }
                    .buttonStyle(.borderedProminent)
                }
                .padding(25)
                .frame(maxWidth: 360)
                .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 20))
                .padding()
            } else if loading {
                VStack(spacing: 12) {
                    ProgressView()
                        .tint(.yellow)
                    Text("Connecting to PAPER trading engine…")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
                .padding(20)
                .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 14))
            }
        }
        .preferredColorScheme(.dark)
    }
}

private struct MobileTerminalWebView: UIViewRepresentable {
    @Binding var connectionError: String?
    @Binding var loading: Bool

    /// A dedicated MOBILE staging server; does not touch the PC app or old server.
    private let terminalURL = URL(string: "https://forex-trading-v1-mobile-2926.onrender.com/")!

    func makeCoordinator() -> Coordinator {
        Coordinator(self)
    }

    func makeUIView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .default()
        let view = WKWebView(frame: .zero, configuration: configuration)
        view.navigationDelegate = context.coordinator
        view.isOpaque = false
        view.backgroundColor = .black
        view.scrollView.backgroundColor = .black
        view.scrollView.contentInsetAdjustmentBehavior = .never
        view.allowsBackForwardNavigationGestures = false
        view.load(URLRequest(
            url: terminalURL,
            cachePolicy: .reloadIgnoringLocalCacheData,
            timeoutInterval: 120
        ))
        return view
    }

    func updateUIView(_ uiView: WKWebView, context: Context) {}

    final class Coordinator: NSObject, WKNavigationDelegate {
        var parent: MobileTerminalWebView

        init(_ parent: MobileTerminalWebView) {
            self.parent = parent
        }

        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            parent.connectionError = nil
            parent.loading = false
        }

        func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
            report(error)
        }

        func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
            report(error)
        }

        private func report(_ error: Error) {
            guard (error as NSError).code != NSURLErrorCancelled else { return }
            parent.loading = false
            parent.connectionError = "Couldn't reach the cloud server. Check your internet connection and try again."
        }

        func webView(
            _ webView: WKWebView,
            decidePolicyFor navigationAction: WKNavigationAction,
            decisionHandler: @escaping (WKNavigationActionPolicy) -> Void
        ) {
            guard let url = navigationAction.request.url else {
                decisionHandler(.cancel)
                return
            }
            if url.host == "forex-trading-v1-mobile-2926.onrender.com" {
                decisionHandler(.allow)
            } else {
                decisionHandler(.cancel)
                UIApplication.shared.open(url)
            }
        }
    }
}

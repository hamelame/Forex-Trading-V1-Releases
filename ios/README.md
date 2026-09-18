# Forex Trading V1 — Native iPhone client

Native SwiftUI client for the existing Render cloud engine. It is deliberately PAPER ONLY and contains no broker-order execution path.

Open the source as an iOS app target in Xcode, set your own bundle identifier/signing team, and build for iPhone. The client polls the cloud API every 3 seconds; the trading engine remains server-side and continues when the iPhone app is closed.

Requires iOS 16+ and an Apple signing identity for installation on a physical iPhone.

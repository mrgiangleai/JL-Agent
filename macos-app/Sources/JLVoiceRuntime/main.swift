import Foundation

@main
struct JLVoiceRuntimeHost {
  static func main() {
    let message = "JL Voice Runtime is installed but microphone activation is not enabled.\n"
    FileHandle.standardError.write(Data(message.utf8))
  }
}

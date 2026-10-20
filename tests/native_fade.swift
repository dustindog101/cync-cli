// Compiled with the production Helper by scripts/test_native.py. No radio or Keychain access.
final class FadeFixture:Helper {
    var writes=[(UInt8,[UInt8])]()
    var queries=0
    override init() {
        super.init()
        runtime=FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try! FileManager.default.createDirectory(at:runtime,withIntermediateDirectories:true)
        profile=["node":1,"id":"synthetic-light"]; session=[UInt8](repeating:0,count:16); mac=[1,2,3,4,5,6]
    }
    deinit { try? FileManager.default.removeItem(at:runtime) }
    override func send(_ op:UInt8,_ params:[UInt8]) -> Bool { writes.append((op,params)); return true }
    override func queryState() { queries+=1 }
    func start(_ brightness:Int,_ target:Int,_ duration:Double) {
        job=["request_id":UUID().uuidString,"command":"fade","values":["brightness":target,"duration":duration]]
        state=["brightness":brightness,"on":brightness>0]
        phase="fadeBaseline"; beginFade()
    }
    func stop() { fadeTimer?.invalidate(); endFadeActivity(); job=nil }
    func report(_ brightness:Int) {
        // A synthetic encrypted status packet exercises the production parser.
        var data=[UInt8](repeating:0,count:20)
        data[7]=0xdc; data[10]=1; data[11]=1; data[12]=UInt8(brightness); data[13]=50
        let iv=Array(mac.prefix(3))+Array(data.prefix(5))+[UInt8](repeating:0,count:8)
        let stream=aes(session!,[0]+Array(iv.prefix(15)))
        for i in 7..<20 { data[i] ^= stream[i-7] }
        parseStatus(data)
    }
}
var failures=[String]()
func check(_ condition:Bool,_ message:String) {
    if !condition { failures.append(message); print("FAIL: \(message)") }
}
func runFor(_ seconds:Double) { RunLoop.current.run(until:Date().addingTimeInterval(seconds)) }
func waitForQuery(_ helper:FadeFixture) {
    let deadline=Date().addingTimeInterval(3)
    while helper.queries==0 && Date()<deadline { runFor(0.05) }
}

do {
    let helper=FadeFixture(); defer { helper.stop() }
    helper.start(0,1,0.6); waitForQuery(helper)
    check(helper.writes.first?.0==0xd0 && helper.writes.first?.1==[1],"fade from off powers on first")
    check(!helper.writes.contains { $0.0==0xd0 && $0.1==[0] },"fade from off never powers off mid-ramp")
    check(helper.writes.last?.0==0xd2 && helper.writes.last?.1==[1],"fade reaches requested low brightness")
    check(helper.queries==1,"completed fade requests a fresh status")
    check(helper.job != nil,"sending the target alone does not report success")
    helper.report(0)
    check(helper.job != nil,"mismatching status cannot finish an on fade")
    helper.report(1)
    check(helper.job==nil && helper.fadeActivity==nil,"matching fresh status finishes and releases activity")
}
do {
    let helper=FadeFixture(); defer { helper.stop() }
    helper.start(100,0,0.6); waitForQuery(helper)
    let off=helper.writes.indices.filter { helper.writes[$0].0==0xd0 && helper.writes[$0].1==[0] }
    check(off==[helper.writes.count-1],"fade to zero powers off only at completion")
    check(helper.expected["on"] as? Bool==false,"fade to zero verifies power off")
}
do {
    let helper=FadeFixture(); defer { helper.stop() }
    helper.start(10,90,0.6)
    helper.report(90)
    check(helper.job != nil,"a matching status during the ramp cannot finish early")
    // Simulate a busy main loop: the next callback must catch up to elapsed time.
    Thread.sleep(forTimeInterval:0.7); helper.fadeTimer?.fire()
    check(helper.writes.last?.1==[90],"delayed timer catches up instead of extending the ramp")
    waitForQuery(helper)
    check(helper.queries==1,"delayed ramp still reaches verification")
}
do {
    let helper=FadeFixture(); defer { helper.stop() }
    helper.start(10,90,0.6)
    helper.fail("DISCONNECTED","Synthetic disconnect")
    let count=helper.writes.count; runFor(0.8)
    check(helper.writes.count==count,"failure stops subsequent fade writes")
    check(helper.fadeActivity==nil && helper.job==nil,"failure releases activity and clears the job")
}
do {
    let helper=FadeFixture(); defer { helper.stop() }
    for duration in [60.0,120.0,299.0,300.0] {
        helper.start(10,90,duration)
        check(abs((helper.fadeTimer?.timeInterval ?? 0)-0.15)<0.001,"normal cadence through five minutes: \(duration)s")
        helper.stop()
    }
    helper.start(10,90,600)
    check(abs((helper.fadeTimer?.timeInterval ?? 0)-0.3)<0.001,"ten-minute fade spaces out 2000 steps")
    helper.disconnect()
    check(helper.fadeTimer==nil && helper.fadeActivity==nil,"disconnect releases timer and activity")
}
do {
    let helper=FadeFixture(); defer { helper.stop() }
    helper.start(20,21,60)
    helper.fadeTimer?.fire(); helper.fadeTimer?.fire()
    check(helper.writes.count==2 && helper.writes.allSatisfy { $0.1==[20] },"normal cadence retains repeated whole-percent steps")
}
do {
    let helper=FadeFixture(); defer { helper.stop() }
    helper.rememberBrightness(42)
    helper.start(42,0,0.2)
    helper.report(10) // A fade-down notification must not replace the restore target.
    waitForQuery(helper); helper.report(0)
    check(helper.previousBrightness()==42,"fade down and off preserve the last positive level")
    let permissions=try! FileManager.default.attributesOfItem(atPath:helper.runtime.appendingPathComponent("brightness-levels.json").path)
    check((permissions[.posixPermissions] as? NSNumber)?.intValue==0o600,"saved brightness file is private")
    let restarted=FadeFixture(); defer { restarted.stop() }
    try! FileManager.default.copyItem(at:helper.runtime.appendingPathComponent("brightness-levels.json"),to:restarted.runtime.appendingPathComponent("brightness-levels.json"))
    restarted.job=["request_id":UUID().uuidString,"command":"fade","values":["restore_brightness":true,"duration":0.2]]
    restarted.state=["brightness":0,"on":false]; restarted.phase="fadeBaseline"; restarted.beginFade()
    waitForQuery(restarted)
    check(restarted.writes.last?.1==[42],"fade on restores the saved level after a helper restart")
    restarted.report(42)
    check(restarted.job==nil,"restored fade on still requires matching fresh status")
    restarted.profile["id"]="different-light"
    check(restarted.previousBrightness()==nil,"brightness memory is isolated by device profile")
}
do {
    let helper=FadeFixture(); defer { helper.stop() }
    helper.ready=true
    helper.job=["request_id":UUID().uuidString,"command":"set","values":["on":false]]
    helper.execute()
    check(helper.phase=="offBaseline" && helper.writes.isEmpty,"ordinary off reads brightness before sending off")
    helper.report(37); runFor(0.05)
    check(helper.previousBrightness()==37 && helper.writes.last?.1==[0],"ordinary off remembers the actual pre-off level")
}
do {
    let helper=FadeFixture(); defer { helper.stop() }
    helper.job=["request_id":UUID().uuidString,"command":"fade","values":["restore_brightness":true,"duration":0.2]]
    helper.state=["brightness":0,"on":false]; helper.phase="fadeBaseline"; helper.beginFade()
    waitForQuery(helper)
    check(helper.writes.last?.1==[100],"fade on uses 100 only when no previous level is known")
}
do {
    let helper=FadeFixture(); defer { helper.stop() }
    try! FileManager.default.createDirectory(at:helper.runtime.appendingPathComponent("brightness-levels.json"),withIntermediateDirectories:true)
    helper.ready=true
    helper.job=["request_id":UUID().uuidString,"command":"set","values":["on":false]]
    helper.execute(); helper.report(37); runFor(0.05)
    check(helper.job==nil && helper.writes.isEmpty,"failed brightness save aborts off before sending writes")
}
final class DeadlineFixture:Helper { override func handleJob() {} }
do {
    let helper=DeadlineFixture()
    helper.runtime=FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    try! FileManager.default.createDirectory(at:helper.runtime,withIntermediateDirectories:true)
    defer { try? FileManager.default.removeItem(at:helper.runtime) }
    let request:[String:Any]=["request_id":UUID().uuidString,"command":"fade","timeout":635.0]
    try! JSONSerialization.data(withJSONObject:request).write(to:helper.runtime.appendingPathComponent("request.json"))
    helper.poll()
    let remaining=helper.deadline-ProcessInfo.processInfo.systemUptime
    check(remaining>634 && remaining<=635,"native deadline preserves all ten-minute fade headroom")
}
if failures.isEmpty { print("PASS: native fade timing, power, verification, and cleanup") }
exit(failures.isEmpty ? 0:1)

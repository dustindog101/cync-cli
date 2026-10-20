// Compiled with the production Helper by scripts/test_native.py. No radio or Keychain access.
final class FadeFixture:Helper {
    var writes=[(UInt8,[UInt8])]()
    var queries=0
    override init() {
        super.init()
        runtime=FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try! FileManager.default.createDirectory(at:runtime,withIntermediateDirectories:true)
        profile=["node":1]; session=[UInt8](repeating:0,count:16); mac=[1,2,3,4,5,6]
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
    helper.start(10,90,600)
    check(abs((helper.fadeTimer?.timeInterval ?? 0)-1.5)<0.001,"ten-minute fade keeps the 400-callback cap")
    helper.disconnect()
    check(helper.fadeTimer==nil && helper.fadeActivity==nil,"disconnect releases timer and activity")
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

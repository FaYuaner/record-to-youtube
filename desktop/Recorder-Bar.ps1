# Native minimize-only recording strip. No listener, HTTP server or debugging port.
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -ReferencedAssemblies @(
    [System.Windows.Forms.Form].Assembly.Location,
    [System.Drawing.Color].Assembly.Location,
    [System.Windows.Automation.AutomationElement].Assembly.Location,
    [System.Windows.Automation.ControlType].Assembly.Location
) -TypeDefinition @'
using System;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.RegularExpressions;
using System.Windows.Forms;
using System.Windows.Automation;
public static class DaiguiRecordingBar {
    [DllImport("user32.dll")] public static extern bool IsWindow(IntPtr window);
    [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr window);
    [DllImport("user32.dll")] private static extern uint GetWindowThreadProcessId(IntPtr window, out uint processId);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] private static extern int GetWindowText(IntPtr window, StringBuilder text, int count);
    [DllImport("user32.dll")] private static extern bool ShowWindow(IntPtr window, int command);
    [DllImport("user32.dll")] private static extern bool SetForegroundWindow(IntPtr window);
    [DllImport("user32.dll")] private static extern uint GetDpiForWindow(IntPtr window);
    [DllImport("user32.dll")] private static extern IntPtr SetThreadDpiAwarenessContext(IntPtr context);
    public static uint ProcessFor(IntPtr window) { uint id; GetWindowThreadProcessId(window, out id); return id; }
    public static string Title(IntPtr window) { var s=new StringBuilder(512); GetWindowText(window,s,s.Capacity); return s.ToString(); }
    public static void Restore(IntPtr window) { ShowWindow(window,9); SetForegroundWindow(window); }
    public static void Run(IntPtr owner) {
        var previous=SetThreadDpiAwarenessContext(new IntPtr(-4));
        try { Application.Run(new Controller(owner)); }
        finally { SetThreadDpiAwarenessContext(previous); }
    }
    private sealed class Strip : Form {
        public bool Exiting;
        protected override bool ShowWithoutActivation { get { return true; } }
        protected override void OnFormClosing(FormClosingEventArgs e) {
            if (!Exiting && e.CloseReason == CloseReason.UserClosing) { e.Cancel=true; Hide(); }
            base.OnFormClosing(e);
        }
    }
    private sealed class Controller : ApplicationContext {
        readonly IntPtr owner; readonly uint ownerProcess;
        readonly Strip bar = new Strip();
        readonly Label dot = new Label(), status = new Label(), clock = new Label();
        readonly Button stop = new Button();
        readonly Timer timer = new Timer();
        readonly Regex recording = new Regex(@"^Daigui Recorder · (录制中|錄製中|Recording) · ([0-9:]+)$");
        string language="录制中", endLabel="结束录制";
        bool stopping; volatile bool invoked, lookupBusy; int requestVersion; DateTime deadline, failureUntil;
        double lastScale;
        public Controller(IntPtr window) {
            owner=window; ownerProcess=ProcessFor(owner);
            bar.Text="今日录制 · 录制条"; bar.FormBorderStyle=FormBorderStyle.None;
            bar.ShowInTaskbar=false; bar.TopMost=true; bar.StartPosition=FormStartPosition.Manual;
            bar.AutoScaleMode=AutoScaleMode.None; bar.BackColor=Color.FromArgb(245,245,247);
            bar.Font=new Font("Microsoft YaHei UI",9.5f);
            dot.Text="●"; dot.ForeColor=Color.FromArgb(255,59,48);
            status.ForeColor=Color.FromArgb(80,80,85); status.Cursor=Cursors.Hand;
            clock.Font=new Font("Segoe UI",11,FontStyle.Bold); clock.ForeColor=Color.FromArgb(29,29,31);
            stop.FlatStyle=FlatStyle.Flat; stop.FlatAppearance.BorderSize=0; stop.BackColor=Color.FromArgb(255,59,48); stop.ForeColor=Color.White;
            stop.Text=endLabel; stop.Cursor=Cursors.Hand; stop.TabStop=true;
            foreach (Control c in new Control[]{dot,status,clock,stop}) bar.Controls.Add(c);
            foreach(Label label in new Label[]{dot,status,clock}) label.TextAlign=ContentAlignment.MiddleLeft;
            status.Click+=(s,e)=>Restore(owner); clock.Click+=(s,e)=>Restore(owner);
            stop.Click+=(s,e)=> {
                if(stopping) return;
                stopping=true; invoked=false; System.Threading.Interlocked.Increment(ref requestVersion); deadline=DateTime.UtcNow.AddSeconds(8); failureUntil=DateTime.MinValue;
                stop.Enabled=false; stop.Text=language=="Recording"?"Stopping…":"结束中…";
                bar.Refresh(); Restore(owner);
            };
            timer.Interval=300; timer.Tick+=(s,e)=>Poll(); timer.Start(); Poll();
        }
        void LayoutBar() {
            double scale=Math.Max(96,GetDpiForWindow(owner))/96.0;
            Func<int,int> px=n=>(int)Math.Round(n*scale);
            if(lastScale!=scale) {
                lastScale=scale; bar.ClientSize=new Size(px(300),px(44));
                dot.SetBounds(px(12),0,px(16),px(44)); status.SetBounds(px(31),0,px(72),px(44));
                clock.SetBounds(px(106),0,px(79),px(44)); stop.SetBounds(px(193),px(6),px(97),px(32));
                using(var path=new GraphicsPath()) {
                    int radius=px(16),w=bar.Width,h=bar.Height;
                    path.AddArc(0,0,radius,radius,180,90); path.AddArc(w-radius,0,radius,radius,270,90);
                    path.AddArc(w-radius,h-radius,radius,radius,0,90); path.AddArc(0,h-radius,radius,radius,90,90); path.CloseFigure();
                    var old=bar.Region;bar.Region=new Region(path);if(old!=null)old.Dispose();
                }
            }
            var area=Screen.FromHandle(owner).WorkingArea;
            bar.Location=new Point(area.Left+(area.Width-bar.Width)/2,area.Top+px(6));
        }
        void Poll() {
            if(!IsWindow(owner)||ProcessFor(owner)!=ownerProcess) { ExitThread(); return; }
            var match=recording.Match(Title(owner));
            if(!match.Success) { if(stopping)System.Threading.Interlocked.Increment(ref requestVersion); bar.Hide(); stopping=false; invoked=false; stop.Enabled=true; return; }
            language=match.Groups[1].Value; endLabel=language=="Recording"?"Stop recording":language=="錄製中"?"結束錄製":"结束录制";
            clock.Text=match.Groups[2].Value;
            if(stopping) {
                if(DateTime.UtcNow>deadline) {
                    stopping=false; System.Threading.Interlocked.Increment(ref requestVersion); stop.Enabled=true; stop.Text=language=="Recording"?"Retry":"重试";
                    status.Text=language=="Recording"?"Use page":"请在页面结束"; failureUntil=DateTime.UtcNow.AddSeconds(10);
                } else if(!invoked && !lookupBusy) {
                    lookupBusy=true; int version=requestVersion; string label=endLabel;
                    // UI Automation runs on an MTA worker, never on the strip's UI thread.
                    System.Threading.ThreadPool.QueueUserWorkItem(ignored=> {
                        try {
                            var root=AutomationElement.FromHandle(owner);
                            var button=root.FindFirst(TreeScope.Descendants,new AndCondition(
                                new PropertyCondition(AutomationElement.ControlTypeProperty,ControlType.Button),
                                new PropertyCondition(AutomationElement.NameProperty,label)));
                            object pattern;
                            if(button!=null && button.Current.IsEnabled && button.TryGetCurrentPattern(InvokePattern.Pattern,out pattern)
                                && version==requestVersion && IsWindow(owner) && ProcessFor(owner)==ownerProcess && recording.IsMatch(Title(owner))) {
                                invoked=true; ((InvokePattern)pattern).Invoke();
                            }
                        } catch(ElementNotAvailableException) { } catch(InvalidOperationException) { } catch(System.Runtime.InteropServices.COMException) { }
                        finally { lookupBusy=false; }
                    });
                }
            } else if(DateTime.UtcNow>=failureUntil) { status.Text=language; stop.Text=endLabel; stop.Enabled=true; }
            if(IsIconic(owner)||stopping||DateTime.UtcNow<failureUntil) {
                LayoutBar(); if(!bar.Visible)bar.Show();
            } else bar.Hide();
        }
        protected override void ExitThreadCore() {
            System.Threading.Interlocked.Increment(ref requestVersion); timer.Stop(); timer.Dispose(); bar.Exiting=true; bar.Close(); bar.Dispose(); base.ExitThreadCore();
        }
    }
}
'@
function Watch-RecorderBar([IntPtr] $OwnerWindow) {
    [DaiguiRecordingBar]::Run($OwnerWindow)
}

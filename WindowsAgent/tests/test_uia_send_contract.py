"""Exercise the native identity rules using the shipping Windows PowerShell."""
import base64,json,os
from pathlib import Path
import subprocess,unittest

@unittest.skipUnless(os.name=='nt','Windows PowerShell contract')
class SendIdentityTests(unittest.TestCase):
    def test_explicit_effort_action_uses_mock_input_and_stops_on_uncertain_readback(self):
        script=Path(__file__).resolve().parents[1]/'uia_reader.ps1'
        source=r"""
$ErrorActionPreference='Stop';$errors=$null;$tokens=$null
$ast=[Management.Automation.Language.Parser]::ParseFile('SCRIPT',[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'parse failed'}
$function=$ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Invoke-ConfirmedEffort'},$true)
. ([scriptblock]::Create($function.Extent.Text))
$script:labels=@('Light','Medium','High','Extra High','Max','Ultra')
$script:position=5;$script:mode='normal';$script:keys=New-Object 'System.Collections.Generic.List[int]'
function Start-Sleep {param($Milliseconds)}
function Get-ComposerEffort {
 $prefix=if($script:mode -eq 'changed-model'){'Other Model'}else{'GPT Fixture Sol'}
 return @{position=$script:position;count=6;label=$script:labels[$script:position-1];name=$prefix+' '+$script:labels[$script:position-1]}
}
function Send-FocusedWindowKey {param($handle,$control,$key)
 if($script:mode -eq 'lost-focus'){throw 'window lost focus'}
 $script:keys.Add($key)
 if($script:mode -eq 'stuck'){return}
 if($script:mode -eq 'jump'){$script:position=3;return}
 if($key -eq 0x25){$script:position--}else{$script:position++}
}
function Invoke-Fixture($target){
 $native=Get-ComposerEffort
 $choice=@{kind='power-step';effort=$native;label=$native.label;element='mock';menuElement='mock-menu'}
 $observed=@{levelCount=6;currentLevel=$script:position;label=$native.label}
 $power=@{targetLevel=$target;labels=$script:labels;targetLabel=$script:labels[$target-1];modelTitle='GPT Fixture Sol'}
 Invoke-ConfirmedEffort 42 $choice $observed $power
}
Invoke-Fixture 1
$left=$script:position -eq 1 -and $script:keys.Count -eq 4 -and @($script:keys|Where-Object {$_ -ne 0x25}).Count -eq 0
$script:keys.Clear();Invoke-Fixture 6
$right=$script:position -eq 6 -and $script:keys.Count -eq 5 -and @($script:keys|Where-Object {$_ -ne 0x27}).Count -eq 0
$script:keys.Clear();Invoke-Fixture 6;$same=$script:keys.Count -eq 0
$script:position=5;$script:mode='stuck';$script:keys.Clear();$stuck=$false
try{Invoke-Fixture 1}catch{$stuck=$_.Exception.Message -eq 'effort pending' -and $script:keys.Count -eq 1}
$script:mode='jump';$script:keys.Clear();$jump=$false
try{Invoke-Fixture 1}catch{$jump=$_.Exception.Message -eq 'effort pending' -and $script:keys.Count -eq 1}
$script:position=5;$script:mode='changed-model';$script:keys.Clear();$model=$false
try{Invoke-Fixture 1}catch{$model=$_.Exception.Message -eq 'option changed' -and $script:keys.Count -eq 0}
$script:mode='lost-focus';$script:keys.Clear();$focus=$false
try{Invoke-Fixture 1}catch{$focus=$_.Exception.Message -eq 'window lost focus' -and $script:keys.Count -eq 0}
$read=$ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Read-ComposerOptions'},$true)
$readOnly=$read.Extent.Text -notmatch 'Send-FocusedWindowKey|Invoke-ConfirmedEffort|keybd_event|SetFocus\('
@{left=$left;right=$right;same=$same;stuck=$stuck;jump=$jump;model=$model;focus=$focus;readOnly=$readOnly}|ConvertTo-Json -Compress
""".replace('SCRIPT',str(script).replace("'","''"))
        shell=Path(os.environ.get('SystemRoot',r'C:\Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'
        result=subprocess.run([str(shell),'-NoLogo','-NoProfile','-NonInteractive','-EncodedCommand',
            base64.b64encode(source.encode('utf-16-le')).decode()],capture_output=True,text=True,encoding='utf-8',timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout),dict(left=True,right=True,same=True,stuck=True,jump=True,model=True,focus=True,readOnly=True))

    def test_shared_ancestors_are_reused_without_crossing_document_boundaries(self):
        script=Path(__file__).resolve().parents[1]/'uia_reader.ps1'
        source=r"""
$ErrorActionPreference='Stop';$errors=$null;$tokens=$null
$ast=[Management.Automation.Language.Parser]::ParseFile('SCRIPT',[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'parse failed'}
$function=$ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Test-PrimaryDocument'},$true)
. ([scriptblock]::Create($function.Extent.Text))
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$AE=[Windows.Automation.AutomationElement];$CT=[Windows.Automation.ControlType]
$script:primaryDocumentCache=@{};$script:parentReads=0
function New-Node($id,$type,$parent){
 $node=[pscustomobject]@{id=$id;parent=$parent;Cached=[pscustomobject]@{ControlType=$type}}
 $node | Add-Member ScriptMethod GetCachedPropertyValue {param($property) return $this.id}
 return $node
}
$walker=[pscustomobject]@{}
$walker | Add-Member ScriptMethod GetParent {param($node,$cache) $script:parentReads++;return $node.parent}
$document=New-Node 'own' $CT::Document $null
$other=New-Node 'other' $CT::Document $null
$shared=New-Node 'shared' $CT::Group $document
$first=Test-PrimaryDocument (New-Node 'a' $CT::Button $shared) 'own'
$second=Test-PrimaryDocument (New-Node 'b' $CT::Button $shared) 'own'
$reused=$script:parentReads -eq 3
$foreign=Test-PrimaryDocument (New-Node 'foreign' $CT::Button $other) 'own'
$anotherScope=Test-PrimaryDocument (New-Node 'c' $CT::Button $shared) 'other'
$script:primaryDocumentCache=@{};$shared.parent=$other
$fresh=Test-PrimaryDocument (New-Node 'a' $CT::Button $shared) 'own'
@{first=$first;second=$second;reused=$reused;foreignBlocked=!$foreign;otherScopeBlocked=!$anotherScope;freshTreeBlocked=!$fresh} | ConvertTo-Json -Compress
""".replace('SCRIPT',str(script).replace("'","''"))
        shell=Path(os.environ.get('SystemRoot',r'C:\Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'
        result=subprocess.run([str(shell),'-NoLogo','-NoProfile','-NonInteractive','-EncodedCommand',
            base64.b64encode(source.encode('utf-16-le')).decode()],capture_output=True,text=True,encoding='utf-8',timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout),dict(first=True,second=True,reused=True,foreignBlocked=True,otherScopeBlocked=True,freshTreeBlocked=True))

    def test_editor_reflow_and_title_changes_keep_the_selected_conversation(self):
        script=Path(__file__).resolve().parents[1]/'uia_reader.ps1'
        source="""
$errors=$null;$tokens=$null
$ast=[Management.Automation.Language.Parser]::ParseFile('SCRIPT',[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'parse failed'}
$function=$ast.Find({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Get-ConversationIdentity'},$true)
. ([scriptblock]::Create($function.Extent.Text))
$guard=$ast.Find({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Confirm-WindowIdentity'},$true)
. ([scriptblock]::Create($guard.Extent.Text))
$script:afterIdentity='target'
function Read-Window {return @{identity=$script:afterIdentity}}
$recovered=Confirm-WindowIdentity 123 @{identity='123|title|'} 'target'
$script:afterIdentity='other';$blocked=$false
try{Confirm-WindowIdentity 123 @{identity='other'} 'target' | Out-Null}catch{$blocked=$_.Exception.Message -eq 'conversation changed'}
$first=Get-ConversationIdentity 123 'document' 'original title' 'old-render-node' @('selected-chat-a')
$reflow=Get-ConversationIdentity 123 'document' 'draft title' 'new-render-node' @('selected-chat-a')
$switch=Get-ConversationIdentity 123 'document' 'original title' 'old-render-node' @('selected-chat-b')
$document=Get-ConversationIdentity 123 'other-document' 'original title' 'old-render-node' @('selected-chat-a')
$fallback=Get-ConversationIdentity 123 'document' 'original title' 'old-render-node' @()
$body=Get-ConversationIdentity 123 'document' 'original title' 'old-render-node' @() 'message-body'
$bodyReflow=Get-ConversationIdentity 123 'document' 'draft title' 'new-render-node' @() 'message-body'
$bodySwitch=Get-ConversationIdentity 123 'document' 'original title' 'old-render-node' @() 'other-message-body'
@{reflowStable=($first -ceq $reflow);switchDetected=($first -cne $switch);documentDetected=($first -cne $document);fallbackGuard=($fallback -ceq '123|original title|old-render-node');bodyStable=($body -ceq $bodyReflow);bodySwitchDetected=($body -cne $bodySwitch);incompleteTreeRecovered=($recovered.identity -ceq 'target');confirmedSwitchBlocked=$blocked} | ConvertTo-Json -Compress
""".replace('SCRIPT',str(script).replace("'","''"))
        shell=Path(os.environ.get('SystemRoot',r'C:\Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'
        result=subprocess.run([str(shell),'-NoLogo','-NoProfile','-NonInteractive','-EncodedCommand',
            base64.b64encode(source.encode('utf-16-le')).decode()],capture_output=True,text=True,encoding='utf-8',timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout),dict(reflowStable=True,switchDetected=True,documentDetected=True,fallbackGuard=True,bodyStable=True,bodySwitchDetected=True,incompleteTreeRecovered=True,confirmedSwitchBlocked=True))

    def test_input_readback_waits_for_settled_content_and_keeps_context_checks(self):
        script=Path(__file__).resolve().parents[1]/'uia_reader.ps1'
        source="""
$ErrorActionPreference='Stop';$errors=$null;$tokens=$null
$ast=[Management.Automation.Language.Parser]::ParseFile('SCRIPT',[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'parse failed'}
foreach($name in @('Normalize-Message','Confirm-WindowIdentity','Wait-ComposerReady')){
 $function=$ast.Find({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name},$true)
 . ([scriptblock]::Create($function.Extent.Text))
}
function New-Snapshot($text,$identity='target'){
 return @{identity=$identity;draft=$text;composer=[pscustomobject]@{};generating=$false;
   senders=@([pscustomobject]@{Current=[pscustomobject]@{Name='Send'}})}
}
$script:reads=0;$script:mode='settling'
function Read-Window {
 $script:reads++
 if($script:mode -eq 'other'){return New-Snapshot 'a b' 'other'}
 if($script:mode -eq 'different'){return New-Snapshot 'different text'}
 return New-Snapshot ('a'+[char]0x00A0+'b')
}
$result=Wait-ComposerReady 123 (New-Snapshot 'intermediate content') 'target' 'a b' $true $true
$settled=$result.draft -ceq ('a'+[char]0x00A0+'b') -and $script:reads -eq 2
$script:mode='other';$changed=$false
try {Wait-ComposerReady 123 (New-Snapshot 'a b' 'other') 'target' 'a b' $true $true | Out-Null}
catch {$changed=$_.Exception.Message -eq 'conversation changed'}
$script:mode='different';$preserved=$false
try {Wait-ComposerReady 123 (New-Snapshot 'different text') 'target' 'a b' $true $true | Out-Null}
catch {$preserved=$_.Exception.Message -eq 'draft changed'}
@{settled=$settled;changedConversationBlocked=$changed;differentContentBlocked=$preserved} | ConvertTo-Json -Compress
""".replace('SCRIPT',str(script).replace("'","''"))
        shell=Path(os.environ.get('SystemRoot',r'C:\Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'
        result=subprocess.run([str(shell),'-NoLogo','-NoProfile','-NonInteractive','-EncodedCommand',
            base64.b64encode(source.encode('utf-16-le')).decode()],capture_output=True,text=True,encoding='utf-8',timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout),dict(settled=True,changedConversationBlocked=True,differentContentBlocked=True))

    def test_model_discovery_is_local_and_power_count_is_scoped_to_its_control(self):
        script=Path(__file__).resolve().parents[1]/'uia_reader.ps1'
        source=r"""
$ErrorActionPreference='Stop';$errors=$null;$tokens=$null
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$AE=[Windows.Automation.AutomationElement];$CT=[Windows.Automation.ControlType]
$ast=[Management.Automation.Language.Parser]::ParseFile('SCRIPT',[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'parse failed'}
foreach($name in @('Get-ComposerControlKind','Get-ComposerEffort','Get-ComposerPosition','Get-VisiblePowerCaption','Get-PowerDisplayLabel','Get-OptionMenu')){
 $function=$ast.Find({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name},$true)
 . ([scriptblock]::Create($function.Extent.Text))
}
$local=(Get-ComposerControlKind 'GPT-6.1 Sol Max' $true) -eq 'model'
$history=(Get-ComposerControlKind 'GPT-6.1 Sol Max' $false) -eq ''
$activity=(Get-ComposerControlKind '读取新账号的模型选择条' $true) -eq ''
$control=[pscustomobject]@{tag='slider';Current=[pscustomobject]@{ClassName='SliderKeyboardControl-fixture';IsKeyboardFocusable=$false;HelpText=''}}
$script:scope=[pscustomobject]@{Current=[pscustomobject]@{ClassName='SimpleView-fixture'}}
$script:scope | Add-Member ScriptMethod FindAll {
 return @([pscustomobject]@{tag='text';Current=[pscustomobject]@{Name=$script:announcement;ClassName=''}})
}
$walker=[pscustomobject]@{}
$walker | Add-Member ScriptMethod GetParent {
 param($item)
 if($item.tag -eq 'slider'){return $script:scope}
 return [pscustomobject]@{Current=[pscustomobject]@{ClassName='SimpleView-fixture'}}
}
$menu=[pscustomobject]@{}
$menu | Add-Member ScriptMethod FindAll {throw 'must not read another menu control'}
$script:structureWalker=$walker
$script:announcement='GPT Fixture Standard, 2 of 3.'
$first=Get-ComposerEffort $menu $control
$script:announcement='GPT-6.1 Sol Max，第 5 项，共 6 项。'
$second=Get-ComposerEffort $menu $control
$control.Current.HelpText='GPT Fixture High, 3 of 4. Use Left and Right arrow keys to adjust power'
$script:structureWalker=[pscustomobject]@{}
$script:structureWalker | Add-Member ScriptMethod GetParent {throw 'direct description must not require a DOM wrapper'}
$direct=Get-ComposerEffort $menu $control
$control.Current.HelpText=''
$flatMenu=[pscustomobject]@{Current=[pscustomobject]@{ControlType=$CT::Menu}}
$flatMenu | Add-Member ScriptMethod FindAll {
 return @([pscustomobject]@{Current=[pscustomobject]@{Name='GPT-6.1 Sol Max，第 5 项，共 6 项。'}})
}
$script:structureWalker=[pscustomobject]@{menu=$flatMenu}
$script:structureWalker | Add-Member ScriptMethod GetParent {param($item) return $this.menu}
$flat=Get-ComposerEffort $flatMenu $control
$flatMenu | Add-Member ScriptMethod FindAll {
 return @([pscustomobject]@{Current=[pscustomobject]@{Name='GPT-6.1 Sol Max，第 5 项，共 6 项。'}},
          [pscustomobject]@{Current=[pscustomobject]@{Name='Other control, 1 of 3.'}})
} -Force
$ambiguous=Get-ComposerEffort $flatMenu $control
function Mock-Text($name,$class,$x,$y,$width,$height){
 return [pscustomobject]@{Current=[pscustomobject]@{Name=$name;ClassName=$class;IsOffscreen=$false;
     BoundingRectangle=[Windows.Rect]::new($x,$y,$width,$height)}}
}
$script:structureWalker=[pscustomobject]@{}
$script:structureWalker | Add-Member ScriptMethod GetParent {param($item) return $null}
$texts=@((Mock-Text 'GPT-6.1 Sol 深度，第 3 项，共 6 项。' 'sr-only' 1029 876 1 1),
         (Mock-Text '高' 'ViewToggleEffortLabel-fixture' 1023 877 14 16),
         (Mock-Text 'GPT-6.1 Sol' '' 995 898 70 16),
         (Mock-Text '快速模式' '' 910 877 40 16))
$caption=Get-VisiblePowerCaption $texts
$captionAmbiguous=Get-VisiblePowerCaption ($texts+@(Mock-Text '另一个标题' 'ViewToggleEffortLabel-fixture' 1000 893 60 12))
$aliasesOk=(Get-PowerDisplayLabel 'GPT-6.1 Sol 深度') -eq '高' -and (Get-PowerDisplayLabel 'GPT-6.1 Sol 标准') -eq '中' -and
 (Get-PowerDisplayLabel 'GPT-6.1 Sol Extended') -eq 'High' -and (Get-PowerDisplayLabel 'GPT-6.1 Sol Extra High') -eq 'Extra High' -and
 (Get-PowerDisplayLabel 'GPT-6.1 Sol 6') -eq ''
$rootMenu=[pscustomobject]@{Cached=[pscustomobject]@{ControlType=$CT::Menu;ClassName='Menu-fixture'}}
$list=[pscustomobject]@{Cached=[pscustomobject]@{ControlType=$CT::List;ClassName='ModelList-fixture'};parent=$rootMenu}
$row=[pscustomobject]@{parent=$list}
$walker=[pscustomobject]@{}
$walker | Add-Member ScriptMethod GetParent {param($item,$cache) return $item.parent}
$headerScope=[object]::ReferenceEquals((Get-OptionMenu $row),$rootMenu)
@{local=$local;historyIgnored=$history;activityIgnored=$activity;
 counts=($first.count -eq 3 -and $second.count -eq 6 -and $second.name -eq 'GPT-6.1 Sol Max');
 combined=($first.kind -eq 'power-step' -and $second.kind -eq 'power-step');headerScope=$headerScope;
 directDescription=($direct.count -eq 4 -and $direct.position -eq 3 -and $direct.name -eq 'GPT Fixture High');
 flattenedNativeView=($flat.count -eq 6 -and $flat.position -eq 5);ambiguousStatusRejected=($null -eq $ambiguous);
 visibleCaption=($caption -eq '高' -and $captionAmbiguous -eq '' -and $aliasesOk)} | ConvertTo-Json -Compress
""".replace('SCRIPT',str(script).replace("'","''"))
        shell=Path(os.environ.get('SystemRoot',r'C:\Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'
        result=subprocess.run([str(shell),'-NoLogo','-NoProfile','-NonInteractive','-EncodedCommand',
            base64.b64encode(source.encode('utf-16-le')).decode()],capture_output=True,text=True,encoding='utf-8',timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout),dict(local=True,historyIgnored=True,activityIgnored=True,counts=True,combined=True,headerScope=True,directDescription=True,flattenedNativeView=True,ambiguousStatusRejected=True,visibleCaption=True))

if __name__=='__main__':unittest.main()

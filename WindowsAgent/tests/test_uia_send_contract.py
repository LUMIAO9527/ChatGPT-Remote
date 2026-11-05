"""Exercise the native identity rules using the shipping Windows PowerShell."""
import base64,json,os
from pathlib import Path
import subprocess,unittest

@unittest.skipUnless(os.name=='nt','Windows PowerShell contract')
class SendIdentityTests(unittest.TestCase):
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

if __name__=='__main__':unittest.main()

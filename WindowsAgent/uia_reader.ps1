# Runs in a private child process. Requests and replies stay in memory.
$ErrorActionPreference = 'Stop'
$utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::InputEncoding = $utf8
[Console]::OutputEncoding = $utf8
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$AE = [System.Windows.Automation.AutomationElement]
$CT = [System.Windows.Automation.ControlType]
$walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker

function Get-WindowTree($handle) {
    $root = $AE::FromHandle([IntPtr][long]$handle)
    if ($null -eq $root) { throw 'window unavailable' }
    $cache = New-Object System.Windows.Automation.CacheRequest
    foreach ($property in @($AE::NameProperty, $AE::ControlTypeProperty,
        $AE::ClassNameProperty, $AE::AutomationIdProperty, $AE::IsPasswordProperty,
        $AE::IsEnabledProperty, $AE::IsKeyboardFocusableProperty, $AE::IsOffscreenProperty,
        $AE::BoundingRectangleProperty, $AE::RuntimeIdProperty)) { $cache.Add($property) }
    $active = $cache.Activate()
    try { $nodes = $root.FindAll([System.Windows.Automation.TreeScope]::Descendants,
                                [System.Windows.Automation.Condition]::TrueCondition) }
    finally { $active.Dispose() }
    $document = $null
    foreach($node in $nodes) { if($node.Cached.ControlType -eq $CT::Document) {$document=$node;break} }
    return @{root=$root; nodes=$nodes;document=$document}
}

function Normalize-Message($value) {
    if ($null -eq $value) { return $null }
    return ($value -replace "`r`n?", "`n" -replace '[\u200B\uFEFF\u2060]', '').TrimEnd([char[]]@(10,13,0))
}

function Get-ComposerDraft($value, $prompt, $sendEnabled) {
    $clean = Normalize-Message $value
    if ($null -eq $clean) { return $null }
    if ([string]::IsNullOrWhiteSpace($clean)) { return '' }
    # Chromium exposes an empty contenteditable's accessible prompt as Value.
    # Actual text equal to the prompt enables Send and remains a real draft.
    if (!$sendEnabled -and $clean -ceq (Normalize-Message $prompt)) { return '' }
    return $clean
}

$script:styleCache=@{}
$script:imageAttachmentCache=@{}
$script:durationNodeCache=@{}
$script:parentCache=New-Object System.Windows.Automation.CacheRequest
$script:parentCache.Add($AE::ClassNameProperty)
$script:parentCache.Add($AE::NameProperty)
$script:parentCache.Add($AE::ControlTypeProperty)
$script:parentCache.Add($AE::RuntimeIdProperty)
function Get-NodeStyle($node, $document) {
    $id=$node.GetCachedPropertyValue($AE::RuntimeIdProperty) -join '.'
    if($script:styleCache.ContainsKey($id)){return $script:styleCache[$id]}
    $style=@{}
    try {
        $pattern=$null
        if($document -and $document.TryGetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern,[ref]$pattern)) {
            $range=$pattern.RangeFromChild($node)
            $size=$range.GetAttributeValue([System.Windows.Automation.TextPattern]::FontSizeAttribute)
            $color=$range.GetAttributeValue([System.Windows.Automation.TextPattern]::ForegroundColorAttribute)
            if($size -is [double]){$style.size=$size}
            if($color -is [int]){$style.color=$color}
        }
    } catch { } # Missing formatting never prevents reading or sending.
    if($script:styleCache.Count -ge 2048){$script:styleCache.Clear()}
    $script:styleCache[$id]=$style
    return $style
}

function Read-Window($handle, $optionsOnly=$false, $includeContent=$true) {
    $tree = Get-WindowTree $handle
    $root = $tree.root
    $nodes = $tree.nodes
    $documentId=if($tree.document){$tree.document.GetCachedPropertyValue($AE::RuntimeIdProperty) -join '.'}else{''}
    $records = New-Object 'System.Collections.Generic.List[object]'
    $composers = New-Object 'System.Collections.Generic.List[object]'
    $senders = New-Object 'System.Collections.Generic.List[object]'
    $title = ''
    $firstMarker = ''
    $generating = $false
    $sendEnabled = $false
    $footer = $false
    $role='';$userStyle=$null;$assistantStyle=$null
    $contentEnded=$false
    $limit = [Math]::Min(6000, $nodes.Count)
    for ($i = 0; $i -lt $limit; $i++) {
        $node = $nodes[$i]
        $c = $node.Cached
        if ($c.IsPassword) { continue }
        if ($null -eq $c.ControlType -or [string]::IsNullOrEmpty($c.ControlType.ProgrammaticName)) { continue }
        $type = $c.ControlType.ProgrammaticName.Replace('ControlType.', '')
        $name = $c.Name
        if ($type -eq 'Document' -and !$title) { $title = $name }
        $isMarker = $c.ClassName -match 'sr-only' -and $name -match '^(你说[：:]|ChatGPT 说[：:]|You said:|ChatGPT said:|用户[：:]|助手[：:])$'
        if (!$firstMarker -and $isMarker) {
            $firstMarker = ($node.GetRuntimeId() -join '.')
        }
        if ($isMarker) { $footer = $false;$role=if($name -match '^(你说|You said|用户)'){'user'}else{'assistant'} }
        if ($type -eq 'Button') {
            if ($name -match '^(停止|停止生成|停止流式传输|Stop|Stop generating|Stop streaming)$' -and $c.IsEnabled -and
                (Test-PrimaryDocument $node $documentId)) { $generating = $true }
            if ($name -match '^(发送|发送消息|发送提示词|排队|排队发送|加入队列|Send|Send message|Send prompt|Queue|Queue message|Queue prompt|Add to queue)$' -and $c.IsEnabled -and !$c.IsOffscreen -and
                (Test-PrimaryDocument $node $documentId)) {
                $sendEnabled = $true
                $senders.Add($node)
            }
            if ($firstMarker -and $name -match '^(复制消息|编辑消息|复制|朗读|评价回复|更多操作|Copy message|Edit message|Copy|Read aloud|More actions)$') { $footer = $true }
            if(!$optionsOnly -and $includeContent -and !$contentEnded -and $role -eq 'assistant' -and $c.IsEnabled -and
                ($name -match '^(复制消息|Copy message)$' -or $c.AutomationId -eq 'copy-turn-action-button')) {
                $records.Add(@{type='Status';complete=$true})
            }
        }
        if(!$optionsOnly -and $includeContent -and !$contentEnded -and $firstMarker -and !$footer -and
            $type -eq 'Image' -and $c.ClassName -notmatch '(^|[ -])icon(-| |$)') {
            $r=$c.BoundingRectangle
            if(!$r.IsEmpty -and $r.Width -ge 64 -and $r.Height -ge 48) {
                $imageId=$node.GetCachedPropertyValue($AE::RuntimeIdProperty) -join '.';$scopeKey=$documentId+'|'+$imageId
                if(!$script:imageAttachmentCache.ContainsKey($scopeKey)){
                    $imageClass=$c.ClassName
                    if($imageClass -notmatch '(?i)object-cover|object-contain|image-attachment|attachment-image|file-thumbnail|image-preview'){
                        $p=$walker.GetParent($node,$script:parentCache);if($p){$imageClass+=' '+$p.Cached.ClassName}
                    }
                    $recognized=($imageClass -match '(?i)object-cover|object-contain|image-attachment|attachment-image|file-thumbnail|image-preview') -and (Test-PrimaryDocument $node $documentId)
                    if($script:imageAttachmentCache.Count -ge 256){$script:imageAttachmentCache.Clear()}
                    $script:imageAttachmentCache[$scopeKey]=$recognized
                }
                if($script:imageAttachmentCache[$scopeKey]){$records.Add(@{type='Attachment';kind='image';id=$imageId})}
            }
        }
        if ($type -eq 'Edit' -and $c.IsEnabled -and $c.IsKeyboardFocusable -and (
            $c.ClassName -match 'ProseMirror' -or $c.AutomationId -eq 'prompt-textarea' -or
            $name -match '^(使用 ChatGPT Work|消息输入框|Message ChatGPT|Ask anything|询问任何问题|向 ChatGPT 发送消息)$') -and
            (Test-PrimaryDocument $node $documentId)) {
            $composers.Add($node)
        }
        if (!$optionsOnly -and $includeContent -and !$contentEnded -and $type -in @('Text', 'Hyperlink', 'Header', 'ListItem', 'Edit')) {
            $parent=$null;$parentClass=''
            if($firstMarker -and $type -ne 'Edit') {
                $parent=$walker.GetParent($node,$script:parentCache)
                if($parent){$parentClass=$parent.Cached.ClassName}
            }
            if ($footer -and !$isMarker -and $type -ne 'Edit' -and $parentClass -notmatch '(^| )thread-scroll-container( |$)|(?i)queued|pending-message|message-queue') { continue }
            $record = @{type=$type; name=$name; marker=$isMarker}
            if($isMarker){$record.sourceId=$documentId+'|'+($node.GetCachedPropertyValue($AE::RuntimeIdProperty) -join '.')}
            # Native elapsed-time labels are top-level metadata. Identical words
            # quoted inside a message remain body text.
            if($type -eq 'Text' -and $parentClass -match '(^| )thread-scroll-container( |$)' -and
                ($name -match '^(?i)(已处理|已思考|思考了|已工作|处理用时|耗时|Worked for|Thought for|Took)\s*(\d+(\.\d+)?\s*(小时|分钟|毫秒|秒|时|分|h(ours?)?|m(in(ute)?s?)?|s(ec(ond)?s?)?|ms)\s*)+[.。]?$' -or $name -match '^(活动|Activity)$')){
                $durationId=$documentId+'|'+($node.GetCachedPropertyValue($AE::RuntimeIdProperty) -join '.')
                if(!$script:durationNodeCache.ContainsKey($durationId)){
                    $rawParent=[Windows.Automation.TreeWalker]::RawViewWalker.GetParent($node,$script:parentCache)
                    $pc=if($rawParent){$rawParent.Cached.ClassName}else{''}
                    $native=$pc -match '(^| )text-size-chat( |$)' -and $pc -match '(^| )overflow-visible( |$)' -and $pc -match '(^| )py-0( |$)'
                    if($native){$gp=[Windows.Automation.TreeWalker]::RawViewWalker.GetParent($rawParent,$script:parentCache);$native=$gp -and $gp.Cached.ClassName -ceq 'relative shrink-0'}
                    if($script:durationNodeCache.Count -ge 256){$script:durationNodeCache.Clear()}
                    $script:durationNodeCache[$durationId]=[bool]$native
                }
                if($script:durationNodeCache[$durationId]){
                    if($name -match '^(活动|Activity)$'){$records.Add(@{type='Status';activity=$true})}
                    else{$record.duration=$true;$records.Add($record)}
                    continue
                }
            }
            if(!$isMarker -and ($c.ClassName+' '+$parentClass) -match '(^|[ _-])(queued-message|pending-message|message-queue)([ _-]|$)') {
                $record.queued=$true;$record.queueId=$parent.GetCachedPropertyValue($AE::RuntimeIdProperty) -join '.'
                if($c.ClassName -match '(^|[ _-])(queue-status|queue-label)([ _-]|$)'){continue}
            }
            if ($firstMarker -and $type -ne 'Edit') {
                $rect = $c.BoundingRectangle
                if (!$rect.IsEmpty -and ![double]::IsInfinity($rect.Top) -and ![double]::IsNaN($rect.Top)) {
                    $record.rect = @{top=[Math]::Round($rect.Top,1); bottom=[Math]::Round($rect.Bottom,1)}
                }
                # Work-mode commentary and tool activity have native containers.
                # Use structure and observed typography, never words in the body.
                if($role -eq 'assistant' -and $parentClass -match '^group flex min-w-0 flex-col$') {$record.channel='activity'}
                elseif($role -eq 'assistant' -and $parentClass -ceq 'outline-none') {$record.channel='activity'}
                elseif(($c.ClassName+' '+$parentClass) -match '(?i)reasoning|thinking|analysis-content') {$record.channel='reasoning'}
                $record.code = ($c.ClassName+' '+$parentClass) -match 'font-mono|CodeBlock|code-block|_Code_'
                if ($type -eq 'Text' -and $parentClass -match '(^| )thread-scroll-container( |$)' -and
                    $name -match '^\d{1,2}:\d{2}(:\d{2})?$') { $record.timestamp = $true }
                if(!$isMarker -and !$record.timestamp -and $type -eq 'Text' -and $name.Trim() -and !$record.code) {
                    if($role -eq 'user' -and !$userStyle) {$userStyle=Get-NodeStyle $node $tree.document}
                    if($role -eq 'assistant' -and !$assistantStyle -and $record.channel -ne 'reasoning') {$assistantStyle=Get-NodeStyle $node $tree.document}
                    if($role -eq 'assistant' -and $parentClass -match '(^| )thread-scroll-container( |$)') {
                        $style=Get-NodeStyle $node $tree.document
                        if($userStyle -and $assistantStyle -and $userStyle.ContainsKey('color') -and $assistantStyle.ContainsKey('color') -and
                            $userStyle.color -ne $assistantStyle.color -and $style.color -eq $userStyle.color -and $style.size -eq $userStyle.size) {
                            # Native pending user text may precede its eventual role marker.
                            $record.queued=$true;$record.queueId='pending-'+($node.GetCachedPropertyValue($AE::RuntimeIdProperty) -join '.')
                        } elseif($assistantStyle -and $style.ContainsKey('size') -and $assistantStyle.ContainsKey('size') -and $style.size -lt $assistantStyle.size) {
                            $record.channel='reasoning'
                        }
                    }
                }
            }
            $records.Add($record)
            if($type -eq 'Edit' -and $composers.Count -gt 0){$contentEnded=$true}
            if ($type -eq 'ListItem') {
                $record.complete = $true
                # The accessible name contains the entire list item; children
                # may expose only styled fragments and must not replace it.
                $i += $node.FindAll([System.Windows.Automation.TreeScope]::Descendants,
                    [System.Windows.Automation.Condition]::TrueCondition).Count
            }
        }
    }
    $composer = $null
    $draft = $null
    if ($composers.Count -eq 1 -and $optionsOnly) { $composer=$composers[0] }
    elseif ($composers.Count -eq 1) {
        $composer = $composers[0]
        $pattern = $null
        if ($composer.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern, [ref]$pattern)) {
            if (!$pattern.Current.IsReadOnly) { $draft = $pattern.Current.Value }
        } else {
            $pattern = $null
            if ($composer.TryGetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern, [ref]$pattern)) {
                $draft = $pattern.DocumentRange.GetText(32001)
            }
        }
    }
    if($composer) {
        $cr=$composer.Cached.BoundingRectangle
        # Exclude similarly named controls in embedded tools and other panes.
        $senders=@($senders | Where-Object {
            $br=$_.Cached.BoundingRectangle
            $br.Left -ge $cr.Left-120 -and $br.Right -le $cr.Right+140 -and
            $br.Top -ge $cr.Top-80 -and $br.Bottom -le $cr.Bottom+140
        })
    } else {$senders=@()}
    $sendEnabled=@($senders).Count -gt 0
    if($composer -and !$optionsOnly){$draft=Get-ComposerDraft $draft $composer.Current.Name $sendEnabled}
    $identity = ([string]$handle) + '|' + $title + '|' + $firstMarker
    return @{records=$records.ToArray(); title=$title; identity=$identity; composer=$composer;
        draft=$draft; generating=$generating; sendEnabled=$sendEnabled; senders=@($senders); truncated=($nodes.Count -gt $limit);tree=$tree}
}

function Get-OptionMenu($node) {
    for ($depth=0; $depth -lt 9; $depth++) {
        $node = $walker.GetParent($node,$script:parentCache)
        if ($null -eq $node) { return $null }
        $c = $node.Cached
        if ($c.ControlType -eq $CT::Menu -or ($c.ControlType -eq $CT::List -and $c.ClassName -match '(?i)select|combo|listbox|dropdown|command|popover') -or
            $c.ClassName -match '(^|[ _-])(dropdown-menu|popover-content|menu-content|select-content|command-list)([ _-]|$)') { return $node }
        if ($c.ControlType -eq $CT::Window -or $c.ControlType -eq $CT::Document) { return $null }
    }
    return $null
}

function Get-ComposerAttachments($snapshot) {
    if(!$snapshot.composer){return @{count=0;names=@();ready=$false}}
    $r=$snapshot.composer.Cached.BoundingRectangle;$removed=0;$images=0;$names=New-Object 'System.Collections.Generic.HashSet[string]'
    $documentId=if($snapshot.tree.document){$snapshot.tree.document.GetCachedPropertyValue($AE::RuntimeIdProperty) -join '.'}else{''}
    foreach($node in $snapshot.tree.nodes){
        $c=$node.Cached;$b=$c.BoundingRectangle
        if($c.IsOffscreen -or $b.IsEmpty -or $b.Bottom -lt $r.Top-300 -or $b.Top -gt $r.Bottom+30 -or $b.Left -lt $r.Left-30 -or $b.Right -gt $r.Right+70){continue}
        if($c.ControlType -eq $CT::Button -and $c.Name -match '(?i)^(移除|删除|Remove|Delete)(\s|附件|文件|图片|attachment|file|image|$)' -and
            (Test-PrimaryDocument $node $documentId)){$removed++}
        elseif($c.ControlType -eq $CT::Image -and $c.ClassName -match '(?i)object-cover|attachment|preview' -and $b.Width -ge 40 -and $b.Height -ge 40){$images++}
        elseif($c.ControlType -in @($CT::Text,$CT::Button) -and $c.Name.Length -le 180 -and $c.Name -notmatch '(?i)^(移除|删除|Remove|Delete)(\s|附件|文件|图片)' -and $c.Name -match '(?i)^[^\\/\r\n]+\.(png|jpe?g|gif|webp|pdf|txt|md|docx?|xlsx?|csv|pptx?|zip|json|py|js|html|svg|heic)$'){
            [void]$names.Add($c.Name)
        }
    }
    # A remove action scopes this to composer attachments rather than a nearby
    # historical image. Unnamed thumbnails alone never confirm a new paste.
    return @{count=$removed;names=@($names);images=$images;ready=($removed -gt 0)}
}

$script:rootDocumentCache=@{}
function Get-ComposerRoot($snapshot, $allowScopeCache=$false) {
    if(!$snapshot.composer){return $null}
    $r=$snapshot.composer.Cached.BoundingRectangle
    $documentId=if($snapshot.tree.document){$snapshot.tree.document.GetRuntimeId() -join '.'}else{''}
    $items=New-Object 'System.Collections.Generic.List[object]'
    foreach($node in $snapshot.tree.nodes){
        $c=$node.Cached;$b=$c.BoundingRectangle
        if($c.ControlType -eq $CT::Menu -and !$c.IsOffscreen){return $null}
        if($c.ControlType -notin @($CT::Button,$CT::ComboBox) -or $c.IsOffscreen -or !$c.Name -or $b.IsEmpty){continue}
        $near=$b.Top -ge $r.Top-90 -and $b.Bottom -le $r.Bottom+110 -and $b.Right -ge $r.Left-80 -and $b.Left -le $r.Right+80
        $kind=''
        if($c.Name -match '(?i)(^gpt[- .]|^o[134]([-. ]|$)|模型|select model|switch model|^model\b)'){$kind='model'}
        elseif($near -and $c.Name -match '(?i)(权限|审批|沙盒|permission|approval|access)'){$kind='permission'}
        elseif($near -and $c.Name -match '(?i)(思考|推理|强度|reason|thinking|^(low|medium|high|extra high|低|中|高|超高|标准|扩展)$)'){$kind='effort'}
        elseif($near -and $c.Name -match '(?i)(^(添加|更多|工具|插件|附件|上传|搜索|应用|连接器)|^(add|attach|tools|plugins|apps|search|connectors)\b)'){$kind='tools'}
        if(!$kind){continue}
        $id=$node.GetCachedPropertyValue($AE::RuntimeIdProperty) -join '.'
        $scopeKey=$documentId+'|'+$id
        $primary=if($allowScopeCache -and $script:rootDocumentCache.ContainsKey($scopeKey)){$script:rootDocumentCache[$scopeKey]}else{Test-PrimaryDocument $node $documentId}
        if($script:rootDocumentCache.Count -gt 128){$script:rootDocumentCache.Clear()}
        $script:rootDocumentCache[$scopeKey]=$primary
        if(!$primary){continue}
        $expanded=$null
        if($node.TryGetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern,[ref]$expanded) -and
            $expanded.Current.ExpandCollapseState -eq [System.Windows.Automation.ExpandCollapseState]::Expanded){return $null}
        $items.Add(@{id=$id;title=$c.Name;kind=$kind;enabled=$c.IsEnabled;selected=$false;element=$node})
    }
    return @{ok=$true;identity=$snapshot.identity;context='';menu=$false;entries=$items.ToArray()}
}

function Test-PrimaryDocument($node, $documentId) {
    if (!$documentId) { return $true }
    for ($depth=0; $depth -lt 32; $depth++) {
        if ($null -eq $node) { return $false }
        if ($node.Cached.ControlType -eq $CT::Document) { return ($node.GetCachedPropertyValue($AE::RuntimeIdProperty) -join '.') -ceq $documentId }
        $node=$walker.GetParent($node,$script:parentCache)
    }
    return $false
}

$script:menuCategory='';$script:menuCategoryContext='';$script:menuCategoryIdentity=''
function Read-ComposerOptions($handle, $category='') {
    $snapshot = Read-Window $handle $true
    $tree = $snapshot.tree
    $documentId=if($null -ne $tree.document) {$tree.document.GetRuntimeId() -join '.'} else {''}
    $entries = New-Object 'System.Collections.Generic.List[object]'
    $menus = New-Object 'System.Collections.Generic.List[object]'
    $composerRect = if ($null -ne $snapshot.composer) { $snapshot.composer.Current.BoundingRectangle } else { $null }
    $anchors=New-Object 'System.Collections.Generic.List[object]'
    $anchorIds=New-Object 'System.Collections.Generic.List[string]'
    if($null -ne $composerRect) {foreach($node in $tree.nodes) {
        $c=$node.Cached;$r=$c.BoundingRectangle
        if($c.ControlType -ne $CT::Button -or $c.IsOffscreen -or $r.IsEmpty -or $r.Top -lt $composerRect.Top-90 -or $r.Bottom -gt $composerRect.Bottom+110) {continue}
        if(!(Test-PrimaryDocument $node $documentId)) {continue}
        $p=$null
        if($node.TryGetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern,[ref]$p) -and $p.Current.ExpandCollapseState -eq [System.Windows.Automation.ExpandCollapseState]::Expanded) {$anchors.Add($r);$anchorIds.Add(($node.GetRuntimeId() -join '.'))}
    }}
    $menuRects=@($tree.nodes | Where-Object {$_.Cached.ControlType -eq $CT::Menu -and !$_.Cached.IsOffscreen} | ForEach-Object {$_.Cached.BoundingRectangle})
    foreach ($node in $tree.nodes) {
        $c = $node.Cached
        if ($c.IsPassword -or $c.IsOffscreen -or !$c.Name -or $null -eq $c.ControlType) { continue }
        $type = $c.ControlType.ProgrammaticName.Replace('ControlType.','')
        # Informational and external links are not composer choices.
        if($c.Name -match '^(?i)(了解更多|了解详情|查看更多说明|Learn more|More info)([\s…\.。!！?？:].*)?$' -or
            $c.ClassName -match '(?i)learn[-_]?more|help[-_]?link|external[-_]?link'){continue}
        if ($type -notin @('Button','ComboBox','MenuItem','ListItem','CheckBox','RadioButton')) { continue }
        $r=$c.BoundingRectangle
        $near=$null -ne $composerRect -and !$composerRect.IsEmpty -and !$r.IsEmpty -and $r.Top -ge $composerRect.Top-90 -and
            $r.Bottom -le $composerRect.Bottom+110 -and $r.Right -ge $composerRect.Left-80 -and $r.Left -le $composerRect.Right+80
        $rootKind=''
        if ($type -in @('Button','ComboBox')) {
            if ($c.Name -match '(?i)(^gpt[- .]|^o[134]([-. ]|$)|模型|select model|switch model|^model\b)') { $rootKind='model' }
            elseif ($near -and $c.Name -match '(?i)(权限|审批|沙盒|permission|approval|access)') { $rootKind='permission' }
            elseif ($near -and $c.Name -match '(?i)(思考|推理|强度|reason|thinking|^(low|medium|high|extra high|低|中|高|超高|标准|扩展)$)') { $rootKind='effort' }
            elseif ($near -and $c.Name -match '(?i)(^(添加|更多|工具|插件|附件|上传|搜索|应用|连接器)|^(add|attach|tools|plugins|apps|search|connectors)\b)') { $rootKind='tools' }
        }
        $insideMenu=$false
        foreach($m in $menuRects) {if(!$r.IsEmpty -and $r.Left -ge $m.Left -and $r.Top -ge $m.Top -and $r.Right -le $m.Right -and $r.Bottom -le $m.Bottom) {$insideMenu=$true;break}}
        $portalPossible=$false
        if($type -in @('Button','ComboBox')) {foreach($a in $anchors) {
            $dx=[Math]::Max(0,[Math]::Max($r.Left-$a.Right,$a.Left-$r.Right));$dy=[Math]::Max(0,[Math]::Max($r.Top-$a.Bottom,$a.Top-$r.Bottom))
            if(!$r.IsEmpty -and $r.Width -le 1600 -and $r.Height -le 200 -and $dx -le 80 -and $dy -le 1000) {$portalPossible=$true;break}
        }}
        if (!$rootKind -and !$insideMenu -and !$portalPossible -and $type -notin @('MenuItem','CheckBox','RadioButton')) {continue}
        if (!(Test-PrimaryDocument $node $documentId)) { continue }
        $menu = Get-OptionMenu $node
        $portalContext=''
        if($null -eq $menu -and $portalPossible) {
            $parent=$walker.GetParent($node)
            # This renderer flattens Add's popup buttons into the document's
            # unnamed root group. The expanded composer trigger scopes them.
            if($null -ne $parent -and $parent.Current.ControlType -eq $CT::Group -and !$parent.Current.ClassName) {
                $owner=$walker.GetParent($parent)
                if($null -ne $owner -and $owner.Current.ControlType -eq $CT::Document -and ($owner.GetRuntimeId() -join '.') -ceq $documentId) {
                    $menu=$parent;$portalContext='portal:'+($anchorIds -join ',')
                }
            }
        }
        $kind = ''
        if ($null -ne $menu) {
            $kind=if ($c.Name -match '(?i)^(强度|推理强度|思考强度|Reasoning effort|Effort)$') {'surface'}
                elseif($c.Name -match '(?i)^(文件和文件夹|添加文件|上传文件|从电脑上传|添加照片和文件|Files and folders|Add files|Upload files|Upload from computer|Add photos .{1,4} files)$') {'upload'} else {'option'}
        }
        else {
            $kind=$rootKind
            if (!$kind) { continue }
        }
        $selected = $false
        $pattern = $null
        if ($node.TryGetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern,[ref]$pattern)) { $selected=$pattern.Current.IsSelected }
        $pattern = $null
        if ($node.TryGetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern,[ref]$pattern)) { $selected=$pattern.Current.ToggleState -eq [System.Windows.Automation.ToggleState]::On }
        $entry=@{id=($node.GetRuntimeId() -join '.');title=$c.Name;kind=$kind;enabled=$c.IsEnabled;selected=$selected;element=$node}
        if ($null -ne $menu) { $entry.context=if($portalContext){$portalContext}else{$menu.GetRuntimeId() -join '.'};$entry.menuElement=$menu;$menus.Add($entry) }
        else { $entries.Add($entry) }
        if ($menus.Count -gt 200 -or $entries.Count -gt 200) { break }
    }
    # The final popup is the innermost active submenu in control-tree order.
    $context = if ($menus.Count) { $menus[$menus.Count-1].context } else { '' }
    $visible = if ($menus.Count) { @($menus | Where-Object {$_.context -ceq $context}) } else { $entries.ToArray() }
    if(!$category -and $context -and $context -ceq $script:menuCategoryContext -and $snapshot.identity -ceq $script:menuCategoryIdentity){$category=$script:menuCategory}
    if(!$context){$category=''}
    if($category -eq 'permission'){$visible=@($visible | Where-Object {$_.title -notmatch '^(?i)(Help|帮助|Documentation|文档|Feedback|反馈)([\s…\.。!！?？:].*)?$'})}
    $script:menuCategory=$category;$script:menuCategoryContext=$context;$script:menuCategoryIdentity=$snapshot.identity
    return @{identity=$snapshot.identity;context=$context;menu=($menus.Count -gt 0);entries=@($visible);category=$category}
}

function Get-ProjectAncestor($node) {
    for ($depth=0; $depth -lt 14; $depth++) {
        $node = $walker.GetParent($node,$script:parentCache)
        if ($null -eq $node) { return $null }
        if($node.Cached.ControlType -eq $CT::Document){return $null}
        if ($node.Cached.ClassName -match '(^| )group/cwd( |$)') { return $node }
    }
    return $null
}

function Export-ComposerOptions($options) {
    $items=@($options.entries | ForEach-Object {@{id=$_.id;title=$_.title;kind=$_.kind;enabled=$_.enabled;selected=$_.selected}})
    return @{ok=$true;identity=$options.identity;context=$options.context;menu=$options.menu;items=$items;category=$options.category}
}

function Read-Navigation($handle) {
    $tree = Get-WindowTree $handle
    $documentId=if($tree.document){$tree.document.GetCachedPropertyValue($AE::RuntimeIdProperty) -join '.'}else{''}
    $checkedScopes=@{}
    $entries = New-Object 'System.Collections.Generic.List[object]'
    foreach ($node in $tree.nodes) {
        $c = $node.Cached
        if ($null -eq $c.ControlType -or [string]::IsNullOrEmpty($c.ControlType.ProgrammaticName)) { continue }
        $type = $c.ControlType.ProgrammaticName.Replace('ControlType.', '')
        if ($type -notin @('Button','Hyperlink') -or !$c.IsEnabled) { continue }
        $kind = ''
        if ($c.ClassName -match '(^| )group/folder-row( |$)') { $kind = 'project' }
        elseif ($c.ClassName -match 'app-action-sidebar-thread-selected') { $kind = 'thread' }
        elseif ($c.ClassName -match 'group/section-toggle' -and $c.Name -in @('项目','最近','Projects','Recents')) { $kind = 'section' }
        elseif ($c.Name -in @('展开显示','显示更多','Show more')) { $kind = 'more' }
        elseif ($c.Name -in @('显示侧边栏','打开侧边栏','Show sidebar','Open sidebar')) { $kind = 'sidebar' }
        elseif ($c.Name -match '^(新聊天|新建对话|新对话|New chat|New thread|New conversation)$' -and !$c.IsOffscreen -and $c.ClassName -match '(^| )sidebar-item( |$)') { $kind='new' }
        elseif ($c.Name -match '^(在 .+ 中开始新聊天|Start (a )?new (chat|thread) in .+)$') { $kind='new' }
        if (!$kind) { continue }
        $project = Get-ProjectAncestor $node
        $scope=if($project){$project}else{$node};$scopeId=$scope.GetCachedPropertyValue($AE::RuntimeIdProperty) -join '.'
        if(!$checkedScopes.ContainsKey($scopeId)){$checkedScopes[$scopeId]=Test-PrimaryDocument $scope $documentId}
        if(!$checkedScopes[$scopeId]){continue}
        if ($kind -eq 'more' -and $null -eq $project) { continue }
        if($kind -eq 'new' -and !$project -and $c.Name -match '^(在 |Start .+ in )'){continue}
        if($kind -eq 'new'){$invoke=$null;if(!$node.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern,[ref]$invoke)){continue}}
        $entry = @{id=($node.GetRuntimeId() -join '.'); kind=$kind; title=$c.Name;
            project=if ($null -ne $project) { $project.GetRuntimeId() -join '.' } else { '' };
            projectTitle=if ($null -ne $project) { $project.Cached.Name } else { '' }; element=$node}
        if($kind -eq 'thread'){$entry.selected=$c.ClassName -match '(^| )bg-primary-ghost-hover( |$)'}
        $pattern = $null
        if ($node.TryGetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern,[ref]$pattern)) {
            $entry.expanded = $pattern.Current.ExpandCollapseState -eq [System.Windows.Automation.ExpandCollapseState]::Expanded
        }
        $entries.Add($entry)
    }
    return @{identity=([string]$handle)+'|'+($tree.root.GetRuntimeId() -join '.'); entries=$entries.ToArray()}
}

function Export-Navigation($nav) {
    $items=@($nav.entries | ForEach-Object {$item=@{};foreach($name in $_.Keys){if($name -ne 'element'){$item[$name]=$_.Item($name)}};$item})
    return @{ok=$true;identity=$nav.identity;items=$items}
}

while ($null -ne ($line = [Console]::ReadLine())) {
    $request = $null
    try {
        $request = $line | ConvertFrom-Json
        if ($request.operation -in @('composer','composer_invoke','composer_dismiss','composer_surface')) {
            $options=$null
            if($request.operation -eq 'composer_invoke' -and !$request.context -and $request.item.kind -in @('model','permission','tools','effort')) {
                $options=Get-ComposerRoot (Read-Window $request.hwnd $true)
            }
            if(!$options){$options = Read-ComposerOptions $request.hwnd}
            if ($request.operation -eq 'composer') {
                $reply=Export-ComposerOptions $options;$reply.request_id=$request.request_id
            } else {
                if ($options.identity -cne $request.identity -or $options.context -cne $request.context) { throw 'options changed' }
                if ($request.operation -eq 'composer_dismiss') {
                    if (!$options.menu) { throw 'menu closed' }
                    $reply=@{ok=$true;request_id=$request.request_id;escape=$true}
                } else {
                    $matches=@($options.entries | Where-Object {$_.id -ceq $request.item.id -and $_.title -ceq $request.item.title -and
                        $_.kind -ceq $request.item.kind -and $_.selected -eq $request.item.selected -and $_.enabled})
                    if ($matches.Count -ne 1) { throw 'option changed' }
                    $element=$matches[0].element
                    if ($request.operation -eq 'composer_surface') {
                        if ($matches[0].kind -ne 'surface') { throw 'surface unavailable' }
                        $r=$element.Current.BoundingRectangle;$m=$matches[0].menuElement.Current.BoundingRectangle
                        if ($r.IsEmpty -or $m.IsEmpty) { throw 'surface unavailable' }
                        $reply=@{ok=$true;request_id=$request.request_id;rect=@{x=$r.X;y=$r.Y;width=$r.Width;height=$r.Height};crop=@{x=$m.X;y=$m.Y;width=$m.Width;height=$m.Height}}
                    } else {
                    if ($matches[0].kind -eq 'surface') { throw 'surface requires pointer' }
                    $pattern=$null
                    if ($element.TryGetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern,[ref]$pattern)) {
                        if ($pattern.Current.ExpandCollapseState -eq [System.Windows.Automation.ExpandCollapseState]::Expanded) { $pattern.Collapse() } else { $pattern.Expand() }
                    } elseif ($element.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern,[ref]$pattern)) { $pattern.Invoke() }
                    elseif ($element.TryGetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern,[ref]$pattern)) { $pattern.Select() }
                    elseif ($element.TryGetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern,[ref]$pattern)) { $pattern.Toggle() }
                    else { throw 'option unavailable' }
                    # Chromium popup contents become visible after the opening animation.
                    Start-Sleep -Milliseconds 160
                    $reply=@{ok=$true;request_id=$request.request_id}
                    if ($request.refresh) {
                        $category=if($request.item.kind -in @('model','permission','tools','effort')){$request.item.kind}else{$options.category}
                        $next=Read-ComposerOptions $request.hwnd $category
                        if(!$next.menu -and !$options.menu -and $request.item.kind -in @('model','permission','tools','effort')) {
                            # A read may straddle the desktop popup animation.
                            # Re-read once; the invocation itself is never repeated.
                            Start-Sleep -Milliseconds 80
                            $next=Read-ComposerOptions $request.hwnd $category
                        }
                        $reply.next=Export-ComposerOptions $next
                    }
                    }
                }
            }
        } elseif ($request.operation -in @('navigation','navigate')) {
            $nav = Read-Navigation $request.hwnd
            if ($request.operation -eq 'navigation') {
                $reply=Export-Navigation $nav;$reply.request_id=$request.request_id
            } else {
                $matches = @($nav.entries | Where-Object { $_.id -ceq $request.target -and $_.kind -ceq $request.kind -and $_.title -ceq $request.title -and
                    $_.project -ceq $request.project -and $_.projectTitle -ceq $request.projectTitle })
                if ($nav.identity -cne $request.identity -or $matches.Count -ne 1) { throw 'navigation changed' }
                $entry = $matches[0]
                $pattern = $null
                $unchanged=$entry.kind -eq 'thread' -and $entry.selected
                if($unchanged) { } elseif ($entry.kind -in @('project','section') -and $entry.element.TryGetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern,[ref]$pattern)) {
                    $isExpanded = $pattern.Current.ExpandCollapseState -eq [System.Windows.Automation.ExpandCollapseState]::Expanded
                    if ($request.expanded -and !$isExpanded) { $pattern.Expand() }
                    elseif (!$request.expanded -and $isExpanded) { $pattern.Collapse() }
                } else {
                    if (!$entry.element.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern,[ref]$pattern)) { throw 'navigation unavailable' }
                    $pattern.Invoke()
                }
                $reply = @{ok=$true; request_id=$request.request_id; kind=$entry.kind;unchanged=[bool]$unchanged}
                if($request.refresh -and $entry.kind -notin @('thread','new')) {
                    if($entry.kind -in @('project','section','more')){Start-Sleep -Milliseconds 70}
                    $next=Read-Navigation $request.hwnd
                    if($entry.kind -in @('project','section') -and $request.expanded -and !(@($next.entries | Where-Object {$_.id -ceq $entry.id -and $_.expanded}).Count)){
                        Start-Sleep -Milliseconds 80;$next=Read-Navigation $request.hwnd
                    }
                    $reply.next=Export-Navigation $next
                }
            }
        } else {
        $snapshot = Read-Window $request.hwnd ($request.operation -eq 'identity') ($request.operation -eq 'read')
        if ($request.operation -eq 'identity') {
            $reply=@{ok=$true;request_id=$request.request_id;identity=$snapshot.identity}
        } elseif ($request.operation -in @('attachment_focus','attachments')) {
            if($snapshot.identity -cne $request.identity){throw 'conversation changed'}
            if(!$snapshot.composer){throw 'composer unavailable'}
            if($request.operation -eq 'attachment_focus'){
                $snapshot.composer.SetFocus()
                if(!$snapshot.composer.Current.HasKeyboardFocus){throw 'composer focus rejected'}
            }
            $reply=@{ok=$true;request_id=$request.request_id;attachments=(Get-ComposerAttachments $snapshot)}
        } elseif ($request.operation -eq 'draft') {
            if ($null -eq $snapshot.composer -or $null -eq $snapshot.draft) { throw 'composer unavailable' }
            $previewLength = [Math]::Min(32000,$snapshot.draft.Length)
            if ($previewLength -lt $snapshot.draft.Length -and $previewLength -gt 0 -and [char]::IsHighSurrogate($snapshot.draft[$previewLength-1])) { $previewLength-- }
            $reply = @{ok=$true; request_id=$request.request_id; identity=$snapshot.identity; title=$snapshot.title;
                text=$snapshot.draft.Substring(0,$previewLength); truncated=($snapshot.draft.Length -gt $previewLength)}
        } elseif ($request.operation -eq 'clear_draft') {
            if ($snapshot.identity -cne $request.identity) { throw 'conversation changed' }
            if ($null -eq $snapshot.composer -or $null -eq $snapshot.draft) { throw 'composer unavailable' }
            if ($snapshot.draft -cne $request.text) { throw 'draft changed' }
            if ($snapshot.draft.Length -gt 0) {
                $value = $null
                if (!$snapshot.composer.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern,[ref]$value) -or $value.Current.IsReadOnly) { throw 'clear unavailable' }
                $value.SetValue('')
            }
            $confirmed = $false
            for ($attempt=0; $attempt -lt 8; $attempt++) {
                $check = Read-Window $request.hwnd $false $false
                if ($check.identity -cne $request.identity) { throw 'clear not confirmed' }
                if ($null -ne $check.draft -and $check.draft.Length -eq 0) { $confirmed=$true; break }
                Start-Sleep -Milliseconds 50
            }
            if (!$confirmed) { throw 'clear not confirmed' }
            $reply = @{ok=$true; request_id=$request.request_id}
        } elseif ($request.operation -eq 'prepare') {
            if ($snapshot.identity -cne $request.identity) { throw 'conversation changed' }
            if ($null -eq $snapshot.composer -or $null -eq $snapshot.draft) { throw 'composer unavailable' }
            if ($snapshot.draft.Length -gt 0) { throw 'draft exists' }
            $snapshot.composer.SetFocus()
            # Focus can synchronously change the page; inspect again before input.
            $check = Read-Window $request.hwnd $false $false
            if ($check.identity -cne $request.identity) { throw 'conversation changed' }
            if ($null -eq $check.composer -or $null -eq $check.draft) { throw 'composer unavailable' }
            if ($check.draft.Length -gt 0) { throw 'draft exists' }
            if (!$check.composer.Current.HasKeyboardFocus) { throw 'composer focus rejected' }
            $reply = @{ok=$true; request_id=$request.request_id;generating=$check.generating}
        } elseif ($request.operation -eq 'submit') {
            $ready = $false
            for ($attempt = 0; $attempt -lt 10; $attempt++) {
                if ($snapshot.identity -cne $request.identity) { throw 'conversation changed' }
                if ($null -eq $snapshot.composer -or $null -eq $snapshot.draft) { throw 'composer unavailable' }
                if ((Normalize-Message $snapshot.draft) -ceq (Normalize-Message $request.text) -and
                    $snapshot.senders.Count -eq 1) { $ready = $true; break }
                Start-Sleep -Milliseconds 70
                $snapshot = Read-Window $request.hwnd $false $false
            }
            if ((Normalize-Message $snapshot.draft) -cne (Normalize-Message $request.text)) { throw 'draft changed' }
            if (!$ready) { throw 'send button unavailable' }
            $invoke = $null
            if (!$snapshot.senders[0].TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern, [ref]$invoke)) {
                throw 'send button unavailable'
            }
            # Exactly one invocation. Never retry an uncertain submission.
            $wasGenerating=$snapshot.generating
            $queueButton=$snapshot.senders[0].Current.Name -match '^(排队|排队发送|加入队列|Queue|Queue message|Queue prompt|Add to queue)$'
            $invoke.Invoke()
            # Keep the mutating call short. A new first message legitimately
            # changes the role-marker identity. Read-back recovery is separate.
            Start-Sleep -Milliseconds 60
            $check = Read-Window $request.hwnd $false $false
            $confirmed = $null -ne $check.draft -and !(Normalize-Message $check.draft)
            $reply = @{ok=$true; request_id=$request.request_id; invoked=$true; confirmed=[bool]$confirmed;
                submission=$(if(!$confirmed){'submitted'}elseif($queueButton -or ($wasGenerating -and $check.generating)){'queued'}else{'sent'})}
        } elseif ($request.operation -eq 'read') {
            $rootOptions=Get-ComposerRoot $snapshot $true
            $reply = @{ok=$true; request_id=$request.request_id; nodes=$snapshot.records;
                title=$snapshot.title; identity=$snapshot.identity; generating=$snapshot.generating;
                composer=@{available=($null -ne $snapshot.composer -and $null -ne $snapshot.draft);
                    hasDraft=($null -ne $snapshot.draft -and $snapshot.draft.Length -gt 0);options=$(if($rootOptions){Export-ComposerOptions $rootOptions}else{$null})};
                truncated=$snapshot.truncated}
        } else { throw 'operation unavailable' }
        }
    } catch {
        # Known errors contain no message text, file contents, or credentials.
        $reason = $_.Exception.Message
        if ($reason -notin @('conversation changed','composer unavailable','draft exists','reply in progress','composer focus rejected',
            'draft changed','send button unavailable','send not confirmed','navigation changed','navigation unavailable',
            'clear unavailable','clear not confirmed')) {
            $reason = 'reader unavailable'
        }
        $reply = @{ok=$false; request_id=$request.request_id; reason=$reason;
            errorKind=$_.Exception.GetBaseException().GetType().Name; errorLine=$_.InvocationInfo.ScriptLineNumber}
    }
    [Console]::WriteLine(($reply | ConvertTo-Json -Depth 8 -Compress))
    [Console]::Out.Flush()
}

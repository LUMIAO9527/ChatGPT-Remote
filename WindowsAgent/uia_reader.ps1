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
$script:controlTree=$null

function Get-WindowTree($handle, $controlsOnly=$false, $contentOnly=$false, $optionsOnly=$false) {
    $root = $AE::FromHandle([IntPtr][long]$handle)
    if ($null -eq $root) { throw 'window unavailable' }
    $cache = New-Object System.Windows.Automation.CacheRequest
    foreach ($property in @($AE::NameProperty, $AE::ControlTypeProperty,
        $AE::ClassNameProperty, $AE::AutomationIdProperty, $AE::IsPasswordProperty,
        $AE::IsEnabledProperty, $AE::IsKeyboardFocusableProperty, $AE::IsOffscreenProperty,
        $AE::BoundingRectangleProperty, $AE::RuntimeIdProperty)) { $cache.Add($property) }
    $active = $cache.Activate()
    $condition=[System.Windows.Automation.Condition]::TrueCondition
    if($controlsOnly -or $contentOnly -or $optionsOnly) {
        $filters=New-Object 'System.Collections.Generic.List[System.Windows.Automation.Condition]'
        $types=@($CT::Document,$CT::Edit,$CT::Button,$CT::Hyperlink,$CT::Header,$CT::ComboBox,$CT::Window,$CT::RadioButton,$CT::CheckBox)
        if($contentOnly){$types+=@($CT::Text,$CT::ListItem,$CT::Image)}
        if($optionsOnly){$types+=@($CT::Menu,$CT::MenuItem,$CT::List,$CT::ListItem)}
        foreach($type in $types) {
            $filters.Add([System.Windows.Automation.PropertyCondition]::new($AE::ControlTypeProperty,$type))
        }
        $filters.Add([System.Windows.Automation.PropertyCondition]::new($AE::ClassNameProperty,'sr-only'))
        $filters.Add([System.Windows.Automation.PropertyCondition]::new($AE::ClassNameProperty,'min-w-0 flex-1 self-center text-start select-text'))
        $condition=[System.Windows.Automation.OrCondition]::new($filters.ToArray())
    }
    try {
        $nodes=$null
        $stamp=[Diagnostics.Stopwatch]::GetTimestamp()
        if($controlsOnly -and $script:controlTree -and $script:controlTree.handle -eq $handle -and
            ($stamp-$script:controlTree.at)/[Diagnostics.Stopwatch]::Frequency -lt 0.75) {
            try {
                $live=New-Object 'System.Collections.Generic.List[System.Windows.Automation.AutomationElement]'
                foreach($node in $script:controlTree.nodes){$live.Add($node.GetUpdatedCache($cache))}
                $nodes=$live
            } catch {$script:controlTree=$null}
        }
        $discovered=$null -eq $nodes
        if($discovered){$nodes=$root.FindAll([System.Windows.Automation.TreeScope]::Descendants,$condition)}
        # Reuse element references briefly, refreshing every property. A removed
        # element rebuilds the list; no draft, focus or enabled state is cached.
        if($controlsOnly){$script:controlTree=@{handle=$handle;at=$(if($discovered){$stamp}else{$script:controlTree.at});nodes=$nodes}}
        else{$script:controlTree=$null}
    }
    finally { $active.Dispose() }
    $document = $null
    foreach($node in $nodes) { if($node.Cached.ControlType -eq $CT::Document) {$document=$node;break} }
    return @{root=$root; nodes=$nodes;document=$document;condition=$condition}
}

function Normalize-Message($value) {
    if ($null -eq $value) { return $null }
    return ($value -replace "`r`n?", "`n" -replace '[\u200B\uFEFF\u2060]', '').TrimEnd([char[]]@(10,13,0))
}

function Get-ComposerDraft($value, $placeholderOnly=$false) {
    $clean = Normalize-Message $value
    if ($null -eq $clean) { return $null }
    if ([string]::IsNullOrWhiteSpace($clean)) { return '' }
    if ($placeholderOnly) { return '' }
    return $clean
}

function Test-ComposerPlaceholder($composer) {
    if($composer.Current.ClassName -notmatch 'ProseMirror'){return $false}
    $placeholder=$false;$content=$false
    $id=$composer.GetRuntimeId() -join '.'
    $texts=$composer.FindAll([Windows.Automation.TreeScope]::Descendants,
        [Windows.Automation.PropertyCondition]::new($AE::ControlTypeProperty,$CT::Text))
    foreach($text in $texts){
        if($text.Current.ClassName -match 'ProseMirror-trailingBreak' -or [string]::IsNullOrWhiteSpace($text.Current.Name)){continue}
        $node=$text;$isPlaceholder=$false
        for($depth=0;$depth -lt 8;$depth++){
            $node=[Windows.Automation.TreeWalker]::RawViewWalker.GetParent($node,$script:parentCache)
            if(!$node -or ($node.GetCachedPropertyValue($AE::RuntimeIdProperty) -join '.') -ceq $id){break}
            if($node.Cached.ClassName -match '(^|\s)(placeholder|is-editor-empty)(\s|$)'){$isPlaceholder=$true;break}
        }
        if($isPlaceholder){$placeholder=$true}else{$content=$true}
    }
    return $placeholder -and !$content
}

function Get-ConversationIdentity($handle, $documentId, $title, $firstMarker, $selectedThreads, $messageAnchor='') {
    $selected=@($selectedThreads | Select-Object -Unique)
    if($documentId -and $selected.Count -eq 1) {
        return ([string]$handle)+'|'+$documentId+'|thread|'+$selected[0]
    }
    if($documentId -and $messageAnchor) {
        return ([string]$handle)+'|'+$documentId+'|message|'+$messageAnchor
    }
    return ([string]$handle)+'|'+$title+'|'+$firstMarker
}

$script:styleCache=@{}
$script:imageAttachmentCache=@{}
$script:durationNodeCache=@{}
$script:parentCache=New-Object System.Windows.Automation.CacheRequest
$script:parentCache.Add($AE::ClassNameProperty)
$script:parentCache.Add($AE::NameProperty)
$script:parentCache.Add($AE::ControlTypeProperty)
$script:parentCache.Add($AE::RuntimeIdProperty)
$script:rawPathCache=@{}
function Get-TextContext($node) {
    $path=New-Object 'System.Collections.Generic.List[object]'
    $cursor=$node
    for($depth=0;$cursor -and $depth -lt 32;$depth++) {
        $id=$cursor.GetCachedPropertyValue($AE::RuntimeIdProperty) -join '.'
        if($script:rawPathCache.ContainsKey($id)) {
            foreach($part in $script:rawPathCache[$id]){$path.Add($part)}
            break
        }
        $c=$cursor.Cached
        $path.Add(@{id=$id;class=$c.ClassName;name=$c.Name;type=$c.ControlType;element=$cursor})
        if($c.ControlType -in @($CT::Document,$CT::Window)){break}
        $cursor=[Windows.Automation.TreeWalker]::RawViewWalker.GetParent($cursor,$script:parentCache)
    }
    $parts=$path.ToArray()
    for($offset=0;$offset -lt $parts.Count;$offset++) {
        if(!$script:rawPathCache.ContainsKey($parts[$offset].id)) {
            $script:rawPathCache[$parts[$offset].id]=@($parts[$offset..($parts.Count-1)])
        }
    }
    $classes=($parts | ForEach-Object {$_.class}) -join ' '
    $context=@{classes=$classes;parentClass=$(if($parts.Count -gt 1){$parts[1].class}else{''});
        transcript=($classes -match '(^| )thread-scroll-container( |$)');channel='answer';blockId='';kind='paragraph';
        strong=($classes -match '(^| )font-(semibold|bold)( |$)');emphasis=($classes -match '(^| )italic( |$)');
        inlineCode=($classes -match '_Code_|inline-code');code=($classes -match 'CodeBlock|code-block|_Pre_');
        user=($classes -match '(^| )bg-user-message( |$)');scopeId='';queueId='';resourceId='';documentId='';href=''}
    foreach($part in $parts) {
        if(!$context.documentId -and $part.type -eq $CT::Document){$context.documentId=$part.id}
        if(!$context.href -and $part.type -eq $CT::Hyperlink) {
            $valuePattern=$null
            if($part.element.TryGetCurrentPattern([Windows.Automation.ValuePattern]::Pattern,[ref]$valuePattern)) {
                $value=$valuePattern.Current.Value;if($value -match '^https?://'){$context.href=$value}
            }
        }
        if(!$context.scopeId -and ($part.class -ceq 'relative shrink-0' -or $part.class -ceq 'group flex min-w-0 flex-col')){$context.scopeId=$part.id}
        if(!$context.resourceId -and $part.class -match '(^| )group/resource-card( |$)'){$context.resourceId=$part.id}
        if(!$context.queueId -and ($part.class -match '(?i)(^|[ _-])(queued-message|pending-message|message-queue)([ _-]|$)' -or
            $part.class -ceq 'min-w-0 flex-1 self-center text-start select-text')){$context.queueId=$part.id}
        if(!$context.blockId -and ($part.class -match '_Paragraph_|markdown-paragraph|_Heading_|markdown-heading|CodeBlock|code-block|_Pre_' -or
            $part.type -eq $CT::Header)){$context.blockId=$part.id}
        if($part.type -eq $CT::ListItem -or $part.class -match '_ListItem_|markdown-list-item') {
            if($context.kind -ne 'list'){$context.blockId=$part.id;$context.kind='list'}
        } elseif($context.kind -ne 'list' -and ($part.type -eq $CT::Header -or $part.class -match '_Heading_|markdown-heading')){$context.kind='heading'}
    }
    if($context.code){$context.kind='code'}
    if($classes -match '(?i)reasoning|thinking|analysis-content'){$context.channel='reasoning'}
    elseif($classes -match '(^| )group flex min-w-0 flex-col( |$)|group/activity-header|group/agent-activity|_Activity_|tool-activity'){$context.channel='activity'}
    return $context
}

function Get-AccessibleRuns($text, $children) {
    $runs=New-Object 'System.Collections.Generic.List[object]';$position=0;$formatted=$false
    foreach($child in $children) {
        if($child.Cached.ControlType -ne $CT::Text -or !$child.Cached.Name){continue}
        $context=Get-TextContext $child
        if(!$context.strong -and !$context.emphasis -and !$context.inlineCode -and !$context.href){continue}
        $fragment=$child.Cached.Name
        $at=$text.IndexOf($fragment,$position,[StringComparison]::Ordinal)
        if($at -lt 0){continue}
        if($at -gt $position){$runs.Add(@{text=$text.Substring($position,$at-$position)})}
        $run=@{text=$fragment}
        foreach($flag in @('strong','emphasis','inlineCode')){if($context[$flag]){$run[$flag]=$true}}
        if($context.href){$run.href=$context.href}
        $runs.Add($run);$position=$at+$fragment.Length;$formatted=$true
    }
    if(!$formatted){return @()}
    if($position -lt $text.Length){$runs.Add(@{text=$text.Substring($position)})}
    return $runs.ToArray()
}
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

function Read-Window($handle, $optionsOnly=$false, $includeContent=$true, $controlsOnly=$false) {
    $script:rawPathCache=@{}
    $tree = Get-WindowTree $handle $controlsOnly ($includeContent -and !$optionsOnly -and !$controlsOnly) $optionsOnly
    $root = $tree.root
    $nodes = $tree.nodes
    $documentId=if($tree.document){$tree.document.GetCachedPropertyValue($AE::RuntimeIdProperty) -join '.'}else{''}
    $records = New-Object 'System.Collections.Generic.List[object]'
    $composers = New-Object 'System.Collections.Generic.List[object]'
    $preferredComposers = New-Object 'System.Collections.Generic.List[object]'
    $senders = New-Object 'System.Collections.Generic.List[object]'
    $promptNodes = New-Object 'System.Collections.Generic.List[object]'
    $selectedThreads = New-Object 'System.Collections.Generic.List[string]'
    $selectedTitle=''
    $title = ''
    $firstMarker = ''
    $messageAnchor = ''
    $generating = $false
    $sendEnabled = $false
    $footer = $false
    $role=''
    $contentEnded=$false
    $skipContentUntil=-1
    # Bound by messages, not the first N page nodes. Long histories must retain
    # the latest response and composer while all control metadata is inspected.
    $limit = $nodes.Count
    $markerIndexes=New-Object 'System.Collections.Generic.List[int]'
    $hasTranscript=$false
    if(!$controlsOnly -and !$optionsOnly -and $includeContent) {
        for($j=0;$j -lt $nodes.Count;$j++) {
            $item=$nodes[$j].Cached
            if($item.ClassName -match 'sr-only' -and $item.Name -match '^(你说[：:]|ChatGPT 说[：:]|You said:|ChatGPT said:|用户[：:]|助手[：:])$' -and
                (Test-PrimaryDocument $nodes[$j] $documentId)){
                $markerIndexes.Add($j)
                if(!$hasTranscript -and (Get-TextContext $nodes[$j]).transcript){$hasTranscript=$true}
            }
        }
    }
    $contentStart=if($markerIndexes.Count -gt 80){$markerIndexes[$markerIndexes.Count-80]}else{0}
    if($nodes.Count -gt 4000 -and $markerIndexes.Count) {
        $tail=@($markerIndexes | Where-Object {$_ -ge $nodes.Count-4000 -and $_ -ge $contentStart})
        $contentStart=if($tail.Count){$tail[0]}else{$markerIndexes[$markerIndexes.Count-1]}
    }
    for ($i = 0; $i -lt $nodes.Count; $i++) {
        $node = $nodes[$i]
        $c = $node.Cached
        if ($c.IsPassword) { continue }
        if ($null -eq $c.ControlType -or [string]::IsNullOrEmpty($c.ControlType.ProgrammaticName)) { continue }
        $type = $c.ControlType.ProgrammaticName.Replace('ControlType.', '')
        $name = $c.Name
        if($type -in @('Window','RadioButton','CheckBox') -or
            ($type -eq 'Button' -and ($name -match $script:promptGrant -or $name -match $script:promptSubmit))){
            if(!$c.IsOffscreen -and !$c.BoundingRectangle.IsEmpty){$promptNodes.Add($node)}
        }
        if ($type -eq 'Document' -and !$title) { $title = $name }
        $isMarker = $c.ClassName -match 'sr-only' -and $name -match '^(你说[：:]|ChatGPT 说[：:]|You said:|ChatGPT said:|用户[：:]|助手[：:])$' -and
            (Test-PrimaryDocument $node $documentId)
        if($type -in @('Button','Hyperlink') -and $c.ClassName -match 'app-action-sidebar-thread-selected' -and
            $c.ClassName -match '(^| )bg-primary-ghost-hover( |$)' -and (Test-PrimaryDocument $node $documentId)) {
            $selectedThreads.Add(($node.GetCachedPropertyValue($AE::RuntimeIdProperty) -join '.'))
            $selectedTitle=$name
        }
        if (!$firstMarker -and $isMarker) {
            $firstMarker = ($node.GetRuntimeId() -join '.')
            # Sidebar virtualization may hide the selected row. Anchor the
            # first message's body instead of its replaceable role heading.
            $body=[Windows.Automation.TreeWalker]::RawViewWalker.GetNextSibling($node,$script:parentCache)
            if($body -and $body.Cached.ControlType -in @($CT::Text,$CT::Edit,$CT::Group,$CT::Custom,$CT::List,$CT::Image)) {
                $messageAnchor=$body.GetCachedPropertyValue($AE::RuntimeIdProperty) -join '.'
            }
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
            if($i -lt $limit -and !$optionsOnly -and $includeContent -and !$contentEnded -and $role -eq 'assistant' -and $c.IsEnabled -and
                ($name -match '^(复制消息|Copy message)$' -or $c.AutomationId -eq 'copy-turn-action-button')) {
                $records.Add(@{type='Status';complete=$true})
            }
        }
        if($i -lt $limit -and !$optionsOnly -and $includeContent -and !$contentEnded -and $firstMarker -and !$footer -and
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
        $isPrompt=$c.AutomationId -eq 'prompt-textarea' -or
            $name -match '^(使用 ChatGPT Work|消息输入框|Message ChatGPT|Ask anything|询问任何问题|向 ChatGPT 发送消息)$'
        if ($type -eq 'Edit' -and $c.IsEnabled -and $c.IsKeyboardFocusable -and !$c.IsOffscreen -and (
            $c.ClassName -match 'ProseMirror' -or $c.AutomationId -eq 'prompt-textarea' -or
            $name -match '^(使用 ChatGPT Work|消息输入框|Message ChatGPT|Ask anything|询问任何问题|向 ChatGPT 发送消息)$') -and
            (Test-PrimaryDocument $node $documentId)) {
            $composers.Add($node)
            if($isPrompt){$preferredComposers.Add($node)}
        }
        if (($firstMarker -or $type -eq 'Edit') -and $i -ge $contentStart -and $i -gt $skipContentUntil -and $i -lt $limit -and !$optionsOnly -and $includeContent -and !$contentEnded -and $type -in @('Text', 'Hyperlink', 'Header', 'ListItem', 'Edit')) {
            $parent=$null;$parentClass=''
            $context=$null
            if($firstMarker -and $type -ne 'Edit') {
                $context=Get-TextContext $node
                $parentClass=$context.parentClass
                if($context.documentId -cne $documentId){continue}
                if($hasTranscript -and !$context.transcript -and !$context.queueId){continue}
            }
            if ($footer -and !$isMarker -and $type -ne 'Edit' -and !$context.blockId -and $context.channel -eq 'answer' -and
                !$context.queueId -and $parentClass -notmatch '(^| )thread-scroll-container( |$)|(^| )text-size-chat( |$)') { continue }
            $record = @{type=$type; name=$name; marker=$isMarker}
            if($type -eq 'Edit'){$record.editorId=$node.GetCachedPropertyValue($AE::RuntimeIdProperty) -join '.'}
            if($type -in @('ListItem','Header') -and !$isMarker -and $firstMarker) {
                # Some providers expose the complete list item only in Name;
                # descendants contain just styled fragments. Keep both facts.
                $childCount=$node.FindAll([Windows.Automation.TreeScope]::Descendants,$tree.condition).Count
                if($childCount -gt 0) {
                    $children=@($nodes[($i+1)..([Math]::Min($i+$childCount,$nodes.Count-1))])
                    $runs=@(Get-AccessibleRuns $name $children)
                    if($runs.Count){$record.runs=$runs}
                    $skipContentUntil=$i+$childCount
                }
            }
            if($isMarker){$record.sourceId=$documentId+'|'+$(if($context.scopeId){$context.scopeId}else{$node.GetCachedPropertyValue($AE::RuntimeIdProperty) -join '.'})}
            # Native elapsed-time labels are top-level metadata. Identical words
            # quoted inside a message remain body text.
            if($type -eq 'Text' -and !$context.blockId -and !$context.user -and $context.classes -match '(^| )text-size-chat( |$)' -and
                ($name -match '^(?i)(已处理|已思考|思考了|已工作|处理用时|耗时|Worked for|Thought for|Took)\s*(\d+(\.\d+)?\s*(小时|分钟|毫秒|秒|时|分|h(ours?)?|m(in(ute)?s?)?|s(ec(ond)?s?)?|ms)\s*)+[.。]?$' -or $name -match '^(活动|Activity)$')){
                if($context.classes -match '(^| )overflow-visible( |$)' -and $context.classes -match '(^| )py-0( |$)'){
                    if($name -match '^(活动|Activity)$'){$records.Add(@{type='Status';activity=$true})}
                    else{$record.duration=$true;$records.Add($record)}
                    continue
                }
            }
            if(!$isMarker -and $context.queueId) {
                $record.queued=$true;$record.queueId=$context.queueId
                if($c.ClassName -match '(^|[ _-])(queue-status|queue-label)([ _-]|$)'){continue}
            }
            if ($firstMarker -and $type -ne 'Edit') {
                $rect = $c.BoundingRectangle
                if (!$rect.IsEmpty -and ![double]::IsInfinity($rect.Top) -and ![double]::IsNaN($rect.Top)) {
                    $record.rect = @{top=[Math]::Round($rect.Top,1); bottom=[Math]::Round($rect.Bottom,1)}
                }
                # Work-mode commentary and tool activity have native containers.
                # Use structure and observed typography, never words in the body.
                if($context.channel -ne 'answer'){$record.channel=$context.channel;if(!$isMarker){$record.role='assistant'}}
                if($context.user -and !$record.queued){$record.role='user'}
                if($record.role -and $context.scopeId){$record.sourceId=$documentId+'|'+$context.scopeId}
                if($context.blockId){$record.blockId=$context.blockId;$record.kind=$context.kind}
                $record.code=$context.code
                if($context.strong){$record.strong=$true};if($context.emphasis){$record.emphasis=$true};if($context.inlineCode){$record.inlineCode=$true}
                if($context.href){$record.href=$context.href}
                if ($type -eq 'Text' -and !$context.blockId -and !$context.user -and
                    $name -match '^(?:(?:今天|昨天|前天|星期[一二三四五六日天]|周[一二三四五六日天]|上午|下午|凌晨|晚上|(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)(?:day)?)\s*)?\d{1,2}:\d{2}(:\d{2})?(?:\s*[AP]M)?$') { $record.timestamp = $true }
                if(!$isMarker -and $context.resourceId -and $name.Trim()) {
                    if(!(@($records | Where-Object {$_.type -eq 'Attachment' -and $_.id -ceq $context.resourceId}).Count)){
                        $records.Add(@{type='Attachment';kind='file';id=$context.resourceId;name=$name})
                    }
                    continue
                }
                if($type -eq 'Hyperlink') {
                    $valuePattern=$null
                    if($node.TryGetCurrentPattern([Windows.Automation.ValuePattern]::Pattern,[ref]$valuePattern)) {
                        $href=$valuePattern.Current.Value
                        if($href -match '^https?://'){$record.href=$href}
                    }
                    $skipContentUntil=$i+$node.FindAll([Windows.Automation.TreeScope]::Descendants,$tree.condition).Count
                }
            }
            $records.Add($record)
            if($type -eq 'Edit' -and $isPrompt -and !$c.IsOffscreen){$contentEnded=$true}
            if ($type -eq 'ListItem') {
                $record.complete = $true
                # The accessible name contains the entire list item; children
                # may expose only styled fragments and must not replace it.
                # Skip duplicate text only. Metadata discovery must still visit
                # sidebar descendants, including the selected conversation.
                $skipContentUntil = $i + $node.FindAll([System.Windows.Automation.TreeScope]::Descendants,$tree.condition).Count
            }
        }
    }
    $composer = $null
    $draft = $null
    if($preferredComposers.Count -eq 1){$composers=$preferredComposers}
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
    foreach($sender in $senders) {
        if($sender.Current.Name -match '^(排队|排队发送|加入队列|Queue|Queue message|Queue prompt|Add to queue)$'){$generating=$true;break}
    }
    if($composer -and !$optionsOnly){$draft=Get-ComposerDraft $draft (Test-ComposerPlaceholder $composer)}
    # Message editors and hidden queue editors are not the sending composer.
    $composerId=if($composer){$composer.GetCachedPropertyValue($AE::RuntimeIdProperty) -join '.'}else{''}
    $visibleRecords=@($records | Where-Object {$_.type -ne 'Edit' -or $_.editorId -ceq $composerId})
    $identity = Get-ConversationIdentity $handle $documentId $title $firstMarker $selectedThreads $messageAnchor
    return @{records=$visibleRecords; title=$title; identity=$identity; composer=$composer;
        draft=$draft; generating=$generating; selectedTitle=$selectedTitle;sendEnabled=$sendEnabled; senders=@($senders);
        truncated=($contentStart -gt 0);tree=$tree;promptNodes=$promptNodes.ToArray()}
}

function Read-Queue($handle, $snapshot=$null) {
    if(!$snapshot){$snapshot=Read-Window $handle $false $false $true}
    if(!$snapshot.tree.document){
        return @{ok=$true;identity=$snapshot.identity;items=@();actions=@();paused=$false;hasDraft=[bool]$snapshot.draft;snapshot=$snapshot}
    }
    $items=New-Object 'System.Collections.Generic.List[object]'
    $actions=New-Object 'System.Collections.Generic.List[object]'
    $seen=@{}
    $paused=$false
    $rawWalker=[System.Windows.Automation.TreeWalker]::RawViewWalker
    $previewCondition=[System.Windows.Automation.PropertyCondition]::new($AE::ClassNameProperty,'min-w-0 flex-1 self-center text-start select-text')
    $buttonCondition=[System.Windows.Automation.PropertyCondition]::new($AE::ControlTypeProperty,$CT::Button)
    $row=$null
    foreach($node in $snapshot.tree.nodes){
        $c=$node.Cached
        if($c.ClassName -cne 'min-w-0 flex-1 self-center text-start select-text' -and $c.ControlType -ne $CT::Edit -and
            !($c.ControlType -eq $CT::Button -and $c.Name -match '^(删除.*(排队|队列).*消息|删除排队消息|Delete queued message|引导|Steer|立即发送|Send now|重试|Retry|排队.*消息.*操作|队列.*消息.*操作|Queued message actions)$')){continue}
        if(!(Test-PrimaryDocument $node ($snapshot.tree.document.GetCachedPropertyValue($AE::RuntimeIdProperty) -join '.'))){continue}
        # Chromium flattens unnamed row containers. Named previews and their
        # following action buttons retain native document order.
        if($c.ClassName -ceq 'min-w-0 flex-1 self-center text-start select-text'){
            $id=$node.GetRuntimeId() -join '.'
            if($seen.ContainsKey($id)){$row=$null;continue};$seen[$id]=$true
            $text='';$pattern=$null
            if($node.TryGetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern,[ref]$pattern)){
                $text=$pattern.DocumentRange.GetText(32001)
            }else{
                $texts=$node.FindAll([System.Windows.Automation.TreeScope]::Descendants,
                    [System.Windows.Automation.PropertyCondition]::new($AE::ControlTypeProperty,$CT::Text))
                $text=(@($texts | ForEach-Object {$_.Current.Name}) -join '')
            }
            if(!$text){$text=$c.Name -replace '^(Preview queued message \d+:\s*|预览排队消息\s*\d+[：:]\s*)',''}
            $row=@{id=$id;text=$text.Trim();actions=(New-Object 'System.Collections.Generic.List[object]')}
            $items.Add($row)
        }elseif($c.ControlType -eq $CT::Edit){$row=$null}
        elseif($row -and $c.ControlType -eq $CT::Button){
            $operation=''
            if($c.Name -match '^(删除.*(排队|队列).*消息|删除排队消息|Delete queued message)$'){$operation='delete'}
            elseif($c.Name -match '^(引导|Steer|立即发送|Send now|重试|Retry)$'){$operation='steer'}
            elseif($c.Name -match '^(排队.*消息.*操作|队列.*消息.*操作|Queued message actions)$'){$operation='edit'}
            if($operation){$row.actions.Add(@{id=(($node.GetRuntimeId() -join '.')+'|'+$operation);operation=$operation;
                enabled=[bool]($c.IsEnabled -and !$c.IsOffscreen);element=$node})}
        }
    }
    foreach($entry in $items){$entry.actions=$entry.actions.ToArray()}
    # Pause text and Resume are adjacent in the native queue header.
    if($items.Count){
        foreach($node in $snapshot.tree.nodes){
            if($node.Cached.ControlType -eq $CT::Button -and $node.Cached.Name -match '^(继续|恢复队列|Resume)$' -and
                $node.Cached.IsEnabled -and !$node.Cached.IsOffscreen){
                $parent=$rawWalker.GetParent($node)
                for($depth=0;$parent -and $depth -lt 4;$depth++){
                    if($parent.Current.Name -match '(队列.*暂停|Queue paused because you interrupted)'){$paused=$true;break}
                    $labels=$parent.FindAll([System.Windows.Automation.TreeScope]::Descendants,
                        [System.Windows.Automation.PropertyCondition]::new($AE::ControlTypeProperty,$CT::Text))
                    if(@($labels | Where-Object {$_.Current.Name -match '(队列.*暂停|Queue paused because you interrupted)'}).Count){$paused=$true;break}
                    $parent=$rawWalker.GetParent($parent)
                }
                if($paused){$actions.Add(@{id=(($node.GetRuntimeId() -join '.')+'|resume');operation='resume';enabled=$true;element=$node});break}
            }
        }
    }
    foreach($node in $snapshot.tree.nodes){
        if($node.Cached.ControlType -eq $CT::Button -and $node.Cached.Name -match '^(停止|停止生成|停止流式传输|Stop|Stop generating|Stop streaming)$' -and
            $node.Cached.IsEnabled -and !$node.Cached.IsOffscreen -and
            (Test-PrimaryDocument $node ($snapshot.tree.document.GetCachedPropertyValue($AE::RuntimeIdProperty) -join '.'))){
            $actions.Add(@{id=(($node.GetRuntimeId() -join '.')+'|stop');operation='stop';enabled=$true;element=$node})
        }
    }
    return @{ok=$true;identity=$snapshot.identity;items=$items.ToArray();actions=$actions.ToArray();paused=$paused;
        hasDraft=[bool]$snapshot.draft;snapshot=$snapshot}
}

function Export-Queue($queue) {
    $items=@($queue.items | ForEach-Object {
        @{id=$_.id;text=$_.text;actions=@($_.actions | ForEach-Object {@{id=$_.id;operation=$_.operation;enabled=$_.enabled}})}
    })
    return @{ok=$true;identity=$queue.identity;paused=$queue.paused;hasDraft=$queue.hasDraft;items=$items;
        actions=@($queue.actions | ForEach-Object {@{id=$_.id;operation=$_.operation;enabled=$_.enabled}})}
}

function Invoke-Queue($request) {
    $script:controlTree=$null
    $queue=Read-Queue $request.hwnd
    if($queue.identity -cne $request.identity){throw 'queue changed'}
    $choices=@($queue.actions | ForEach-Object {@{row=$null;action=$_}})
    foreach($row in $queue.items){foreach($a in $row.actions){$choices+=@{row=$row;action=$a}}}
    $matches=@($choices | Where-Object {$_.action.id -ceq $request.target -and $_.action.operation -ceq $request.action -and $_.action.enabled -and
        ((!$_.row -and !$request.item) -or ($_.row.id -ceq $request.item -and $_.row.text -ceq $request.text))})
    if($matches.Count -ne 1){throw 'queue changed'}
    $choice=$matches[0];$element=$choice.action.element;$pattern=$null
    if($request.action -eq 'edit' -and $queue.hasDraft){throw 'queue draft exists'}
    if(!$element.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern,[ref]$pattern)){throw 'queue unavailable'}
    $pattern.Invoke()
    try{
        if($request.action -eq 'edit'){
            Start-Sleep -Milliseconds 120
            $check=Read-Window $request.hwnd $false $false $true
            if($check.identity -cne $request.identity){throw 'queue pending'}
            $menuItems=$check.tree.root.FindAll([System.Windows.Automation.TreeScope]::Descendants,
                [System.Windows.Automation.PropertyCondition]::new($AE::ControlTypeProperty,$CT::MenuItem))
            $edit=@($menuItems | Where-Object {$_.Current.Name -match '^(编辑消息|编辑|Edit message)$' -and $_.Current.IsEnabled -and !$_.Current.IsOffscreen})
            if($edit.Count -ne 1){throw 'queue pending'}
            $pattern=$null
            if(!$edit[0].TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern,[ref]$pattern)){throw 'queue pending'}
            $pattern.Invoke()
            Start-Sleep -Milliseconds 120
            $check=Read-Window $request.hwnd $false $false $true
            if($check.identity -cne $request.identity -or $null -eq $check.draft -or !$check.composer){throw 'queue pending'}
            return @{ok=$true;edited=$true;identity=$check.identity;text=$check.draft;title=$check.title;truncated=$false}
        }
        Start-Sleep -Milliseconds 120
        return @{ok=$true}
    }catch{throw 'queue pending'}
}

function Get-OptionMenu($node) {
    for ($depth=0; $depth -lt 9; $depth++) {
        $node = $walker.GetParent($node,$script:parentCache)
        if ($null -eq $node) { return $null }
        $c = $node.Cached
        if($c.ControlType -eq $CT::List){
            $selection=$null
            if($node.TryGetCurrentPattern([Windows.Automation.SelectionPattern]::Pattern,[ref]$selection)){return $node}
        }
        if ($c.ControlType -eq $CT::Menu -or ($c.ControlType -eq $CT::List -and $c.ClassName -match '(?i)select|combo|listbox|dropdown|command|popover') -or
            $c.ClassName -match '(^|[ _-])(dropdown-menu|popover-content|menu-content|select-content|command-list)([ _-]|$)') { return $node }
        if ($c.ControlType -eq $CT::Window -or $c.ControlType -eq $CT::Document) { return $null }
    }
    return $null
}

function Confirm-WindowIdentity($handle, $current, $identity) {
    $wait=[Diagnostics.Stopwatch]::StartNew();$previous=''
    while($current.identity -cne $identity) {
        # Chromium briefly omits history nodes while replacing a role heading.
        # Refresh discovery instead of treating an incomplete tree as a switch.
        $incomplete=$current.identity.EndsWith('|')
        if($wait.ElapsedMilliseconds -ge 500 -or (!$incomplete -and $previous -ceq $current.identity)){throw 'conversation changed'}
        $previous=$current.identity;$script:controlTree=$null
        Start-Sleep -Milliseconds 40
        $current=Read-Window $handle $false $false $true
    }
    return $current
}

function Get-ComposerAttachments($snapshot) {
    if(!$snapshot.composer){return @{count=0;names=@();ready=$false}}
    $r=$snapshot.composer.Cached.BoundingRectangle;$removed=0;$images=0;$busy=$false;$names=New-Object 'System.Collections.Generic.HashSet[string]'
    $documentId=if($snapshot.tree.document){$snapshot.tree.document.GetCachedPropertyValue($AE::RuntimeIdProperty) -join '.'}else{''}
    $scope=$null;$parent=$snapshot.composer;$bodyFound=$false
    for($depth=0;$depth -lt 7;$depth++){
        $parent=[Windows.Automation.TreeWalker]::RawViewWalker.GetParent($parent,$script:parentCache)
        if(!$parent -or $parent.Cached.ControlType -eq $CT::Document){break}
        $class=$parent.Cached.ClassName
        if($class -match '(^|\s)composer-layout(\s|$)'){$scope=$parent;break}
        if($class -match '_ComposerLayoutBody_'){$bodyFound=$true}
        if($bodyFound -and $class -match '(^|\s)flex-col(\s|$)' -and $class -match '(^|\s)gap-2(\s|$)'){$scope=$parent;break}
    }
    $nodes=if($scope){$scope.FindAll([Windows.Automation.TreeScope]::Descendants,[Windows.Automation.Condition]::TrueCondition)}else{$snapshot.tree.nodes}
    foreach($node in $nodes){
        $c=$node.Current;$b=$c.BoundingRectangle
        if($c.IsOffscreen -or $b.IsEmpty){continue}
        if(!$scope -and ($b.Bottom -lt $r.Top-300 -or $b.Top -gt $r.Bottom+30 -or $b.Left -lt $r.Left-30 -or $b.Right -gt $r.Right+70)){continue}
        if($scope -and ($c.ControlType -eq $CT::ProgressBar -or ($c.ControlType -eq $CT::StatusBar -and $c.Name -match '(?i)上传中|正在上传|uploading|processing'))){$busy=$true}
        if($c.ControlType -eq $CT::Button -and $c.Name -match '(?i)^(移除|删除|Remove|Delete)(\s|附件|文件|图片|attachment|file|image|$)' -and
            ($scope -or (Test-PrimaryDocument $node $documentId))){$removed++}
        elseif($c.ControlType -eq $CT::Image -and $c.ClassName -match '(?i)object-cover|attachment|preview' -and $b.Width -ge 40 -and $b.Height -ge 40){$images++}
        elseif($c.ControlType -in @($CT::Text,$CT::Button) -and $c.Name.Length -le 180 -and $c.Name -notmatch '(?i)^(移除|删除|Remove|Delete)(\s|附件|文件|图片)' -and $c.Name -match '(?i)^[^\\/\r\n]+\.(png|jpe?g|gif|webp|pdf|txt|md|docx?|xlsx?|csv|pptx?|zip|json|py|js|html|svg|heic)$'){
            [void]$names.Add($c.Name)
        }
    }
    $count=if($scope){[Math]::Max($removed,[Math]::Max($images,$names.Count))}else{$removed}
    return @{count=$count;names=@($names);images=$images;ready=($count -gt 0 -and !$busy)}
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

$script:promptGrant='(?i)^(Allow(?: once| always| for this session)?|Always allow|Approve(?: once| for this session)?|Yes,? (?:allow|proceed)|允许(?:一次|本次|此操作|此会话|本会话)?|始终允许|批准(?:一次|本次|本会话|此会话)?|同意(?:本次|执行)?)$'
$script:promptDeny='(?i)^(Deny|Decline|Reject|Cancel|No(?:, suggest changes)?|拒绝(?:本次|请求)?|不允许|取消|不同意|否)$'
$script:promptSubmit='(?i)^(提交|继续|确认|完成|下一步|Submit|Continue|Confirm|Done|Next)$'
$script:promptClass='(?i)(^|[ /_-])(approval-card|approval-request|request-user-input|user-input-request|tool-confirmation|mcp-elicitation)([ /_-]|$)'
$script:promptCache=New-Object System.Windows.Automation.CacheRequest
foreach($property in @($AE::ControlTypeProperty,$AE::NameProperty,$AE::IsEnabledProperty,$AE::IsOffscreenProperty,
                       $AE::IsPasswordProperty,$AE::RuntimeIdProperty,$AE::BoundingRectangleProperty)){
    $script:promptCache.Add($property)
}

function Read-PromptRegion($region,$identity,$explicit) {
    $types=@($CT::Document,$CT::Button,$CT::RadioButton,$CT::CheckBox,$CT::Edit,$CT::Text,$CT::Header)
    $filters=[System.Windows.Automation.Condition[]]@($types | ForEach-Object {
        [System.Windows.Automation.PropertyCondition]::new($AE::ControlTypeProperty,$_)
    })
    $active=$script:promptCache.Activate()
    try { $nodes=$region.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.OrCondition]::new($filters)) }
    finally { $active.Dispose() }
    if($nodes.Count -gt 140){return $null}
    $actions=New-Object 'System.Collections.Generic.List[object]'
    $notes=New-Object 'System.Collections.Generic.List[string]'
    foreach($node in $nodes){
        $c=$node.Cached
        if($c.ControlType -eq $CT::Document -or $c.IsPassword){return $null}
        if($c.IsOffscreen -or $c.BoundingRectangle.IsEmpty -or !$c.Name){continue}
        if($c.ControlType -in @($CT::Text,$CT::Header)){
            if(!$notes.Contains($c.Name)){$notes.Add($c.Name)}
            continue
        }
        if($c.ControlType -notin @($CT::Button,$CT::RadioButton,$CT::CheckBox) -or $c.Name.Length -gt 500){continue}
        $pattern=$null;$selected=$false
        if($node.TryGetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern,[ref]$pattern)){$selected=$pattern.Current.IsSelected}
        elseif($node.TryGetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern,[ref]$pattern)){$selected=$pattern.Current.ToggleState -eq [System.Windows.Automation.ToggleState]::On}
        $actions.Add(@{id=($node.GetCachedPropertyValue($AE::RuntimeIdProperty) -join '.');title=$c.Name;
            kind='option';enabled=$c.IsEnabled;selected=$selected;element=$node})
    }
    if(!$actions.Count -or $actions.Count -gt 24){return $null}
    $approval=($actions.title -match $script:promptGrant) -and ($actions.title -match $script:promptDeny)
    if(!$explicit -and (!$approval -or !$notes.Count)){return $null}
    # An inline approval is recognized only when allow and refuse share this region.
    if(!$explicit){$actions=@($actions | Where-Object {$_.title -match $script:promptGrant -or $_.title -match $script:promptDeny})}
    $title=[string]$region.Current.Name
    if(!$title){$title=if($approval){'电脑审批'}else{'电脑待选项'}}
    $details=($notes | Where-Object {$_ -cne $title}) -join "`n"
    if($title.Length -gt 240){$title=$title.Substring(0,240-[int][char]::IsHighSurrogate($title[239]))}
    # Bind the full visible request to the action, even when the phone preview is shorter.
    $hash=[Security.Cryptography.SHA256]::Create()
    try { $revision=[Convert]::ToBase64String($hash.ComputeHash($utf8.GetBytes($region.Current.Name+"`n"+$details))) }
    finally { $hash.Dispose() }
    $truncated=$details.Length -gt 8000
    if($truncated){$details=$details.Substring(0,8000-[int][char]::IsHighSurrogate($details[7999]))}
    return @{ok=$true;identity=$identity;context=('prompt:'+($region.GetRuntimeId() -join '.')+':'+$revision);
        menu=$false;entries=@($actions | ForEach-Object {$_});category='prompt';
        prompt=@{title=$title;text=$details;approval=[bool]$approval;truncated=$truncated}}
}

function Read-DesktopPrompt($snapshot) {
    $tree=$snapshot.tree
    if(!$tree.document){return $null}
    $documentId=$tree.document.GetRuntimeId() -join '.'
    $seen=New-Object 'System.Collections.Generic.HashSet[string]'
    for($index=$snapshot.promptNodes.Count-1;$index -ge 0;$index--){
        $node=$snapshot.promptNodes[$index];$c=$node.Cached
        if($c.ControlType -eq $CT::Window -and (Test-PrimaryDocument $node $documentId)){
            $prompt=Read-PromptRegion $node $snapshot.identity $true
            if($prompt){return $prompt}
        }
        if($c.ControlType -notin @($CT::Button,$CT::RadioButton,$CT::CheckBox)){continue}
        $grant=$c.ControlType -eq $CT::Button -and $c.Name -match $script:promptGrant
        if(!(Test-PrimaryDocument $node $documentId)){continue}
        for($depth=0;$depth -lt 8;$depth++){
            $node=[Windows.Automation.TreeWalker]::RawViewWalker.GetParent($node,$script:parentCache)
            if(!$node -or $node.Cached.ControlType -in @($CT::Window,$CT::Document)){break}
            $explicit=$node.Cached.ClassName -match $script:promptClass
            if(!$explicit -and (!$grant -or $node.Cached.ClassName -match 'thread-scroll-container')){continue}
            $id=$node.GetCachedPropertyValue($AE::RuntimeIdProperty) -join '.'
            if(!$seen.Add($id)){continue}
            $rect=$node.Current.BoundingRectangle
            if($rect.IsEmpty -or (!$explicit -and $rect.Height -gt 540)){continue}
            $prompt=Read-PromptRegion $node $snapshot.identity $explicit
            if($prompt){return $prompt}
        }
    }
    return $null
}

$script:menuCategory='';$script:menuCategoryContext='';$script:menuCategoryIdentity=''
function Read-ComposerOptions($handle, $category='') {
    $snapshot = Read-Window $handle $true
    $prompt=Read-DesktopPrompt $snapshot
    if($prompt){return $prompt}
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
        if (!$rootKind -and !$insideMenu -and !$portalPossible -and $type -notin @('MenuItem','ListItem','CheckBox','RadioButton')) {continue}
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
    $result=@{ok=$true;identity=$options.identity;context=$options.context;menu=$options.menu;items=$items;category=$options.category}
    if($options.prompt){$result.prompt=$options.prompt}
    return $result
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
        if($request.operation -in @('queue','queue_invoke')){
            if($request.operation -eq 'queue'){$script:controlTree=$null;$reply=Export-Queue (Read-Queue $request.hwnd)}
            else{$reply=Invoke-Queue $request}
            $reply.request_id=$request.request_id
        } elseif ($request.operation -in @('composer','composer_invoke','composer_dismiss','composer_surface')) {
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
                        if(!$next.menu -and !$next.prompt -and !$options.menu -and $request.item.kind -in @('model','permission','tools','effort')) {
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
        $compact=$request.operation -in @('observe','identity','prepare','input_ready','submit','draft','clear_draft','attachments','attachment_focus')
        # Paste may create a new Send control; discover it before read-back.
        if($request.operation -eq 'input_ready' -or $request.fresh){$script:controlTree=$null}
        $snapshot = Read-Window $request.hwnd ($request.operation -eq 'identity') ($request.operation -eq 'read') $compact
        if ($request.operation -eq 'observe') {
            $rootOptions=Get-ComposerRoot $snapshot $true
            $prompt=$null
            try {$prompt=Read-DesktopPrompt $snapshot} catch [System.Windows.Automation.ElementNotAvailableException] {}
            $reply=@{ok=$true;request_id=$request.request_id;identity=$snapshot.identity;selectedTitle=$snapshot.selectedTitle;
                generating=$snapshot.generating;queue=(Export-Queue (Read-Queue $request.hwnd $snapshot));composer=@{available=($null -ne $snapshot.composer -and $null -ne $snapshot.draft);
                hasDraft=($null -ne $snapshot.draft -and $snapshot.draft.Length -gt 0);options=$(if($rootOptions){Export-ComposerOptions $rootOptions}else{$null})}}
            if($prompt){$reply.prompt=Export-ComposerOptions $prompt}
        } elseif ($request.operation -eq 'identity') {
            $reply=@{ok=$true;request_id=$request.request_id;identity=$snapshot.identity}
        } elseif ($request.operation -in @('attachment_focus','attachments')) {
            if($snapshot.identity -cne $request.identity){throw 'conversation changed'}
            if(!$snapshot.composer){throw 'composer unavailable'}
            if($request.operation -eq 'attachment_focus'){
                $snapshot.composer.SetFocus()
                $focusWait=[Diagnostics.Stopwatch]::StartNew()
                while(!$snapshot.composer.Current.HasKeyboardFocus){
                    if($focusWait.ElapsedMilliseconds -ge 500){throw 'composer focus rejected'}
                    Start-Sleep -Milliseconds 15
                    $snapshot=Read-Window $request.hwnd $false $false $true
                    $snapshot=Confirm-WindowIdentity $request.hwnd $snapshot $request.identity
                    if(!$snapshot.composer){throw 'composer unavailable'}
                }
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
                $check = Read-Window $request.hwnd $false $false $true
                if ($check.identity -cne $request.identity) { throw 'clear not confirmed' }
                if ($null -ne $check.draft -and $check.draft.Length -eq 0) { $confirmed=$true; break }
                Start-Sleep -Milliseconds 50
            }
            if (!$confirmed) { throw 'clear not confirmed' }
            $reply = @{ok=$true; request_id=$request.request_id}
        } elseif ($request.operation -eq 'prepare') {
            $snapshot=Confirm-WindowIdentity $request.hwnd $snapshot $request.identity
            if ($null -eq $snapshot.composer -or $null -eq $snapshot.draft) { throw 'composer unavailable' }
            if ($snapshot.draft.Length -gt 0) { throw 'draft exists' }
            $snapshot.composer.SetFocus()
            # Focus can synchronously change the page; inspect again before input.
            $focusWait=[Diagnostics.Stopwatch]::StartNew()
            do {
            $check = Read-Window $request.hwnd $false $false $true
            $check=Confirm-WindowIdentity $request.hwnd $check $request.identity
            if ($null -eq $check.composer -or $null -eq $check.draft) { throw 'composer unavailable' }
            if ($check.draft.Length -gt 0) { throw 'draft exists' }
            if($check.composer.Current.HasKeyboardFocus){break}
            if($focusWait.ElapsedMilliseconds -ge 500){throw 'composer focus rejected'}
            Start-Sleep -Milliseconds 15
            } while($true)
            $reply = @{ok=$true; request_id=$request.request_id;generating=$check.generating}
        } elseif ($request.operation -in @('submit','input_ready')) {
            $ready = $false
            $readyState=$null
            $inputWait=[Diagnostics.Stopwatch]::StartNew()
            $expectedText=Normalize-Message $request.text
            do {
                $snapshot=Confirm-WindowIdentity $request.hwnd $snapshot $request.identity
                if ($null -eq $snapshot.composer -or $null -eq $snapshot.draft) { throw 'composer unavailable' }
                $actualText=Normalize-Message $snapshot.draft
                if ($actualText -ceq $expectedText -and
                    ($snapshot.senders.Count -eq 1 -or ($request.operation -eq 'input_ready' -and !$request.submit))) {
                    $state=([string]$snapshot.generating)+'|'+($snapshot.senders | ForEach-Object {$_.Current.Name})
                    # Chromium exposes new editor text before its queue/button
                    # metadata catches up. Read two settled states before a
                    # keyboard submission so a new reply uses native queuing.
                    if($request.operation -ne 'input_ready' -or $readyState -ceq $state){$ready=$true;break}
                    $readyState=$state
                } else {$readyState=$null}
                if($actualText -cne $expectedText -and !$expectedText.StartsWith($actualText,[StringComparison]::Ordinal)) { throw 'draft changed' }
                if($inputWait.ElapsedMilliseconds -ge 3000){break}
                Start-Sleep -Milliseconds 70
                $snapshot = Read-Window $request.hwnd $false $false $true
            } while($true)
            if ((Normalize-Message $snapshot.draft) -cne (Normalize-Message $request.text)) { throw 'draft changed' }
            if (!$ready) { throw 'send button unavailable' }
            if($request.operation -eq 'input_ready') {
                $reply=@{ok=$true;request_id=$request.request_id;generating=$snapshot.generating}
            } else {
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
            $check = Read-Window $request.hwnd $false $false $true
            $confirmed = $null -ne $check.draft -and !(Normalize-Message $check.draft)
            $reply = @{ok=$true; request_id=$request.request_id; invoked=$true; confirmed=[bool]$confirmed;
                submission=$(if(!$confirmed){'submitted'}elseif($queueButton -or ($wasGenerating -and $check.generating)){'queued'}else{'sent'})}
            }
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
            'queue changed','queue unavailable','queue pending','queue draft exists',
            'clear unavailable','clear not confirmed')) {
            $reason = 'reader unavailable'
        }
        $reply = @{ok=$false; request_id=$request.request_id; reason=$reason;
            errorKind=$_.Exception.GetBaseException().GetType().Name; errorLine=$_.InvocationInfo.ScriptLineNumber}
    }
    [Console]::WriteLine(($reply | ConvertTo-Json -Depth 8 -Compress))
    [Console]::Out.Flush()
}

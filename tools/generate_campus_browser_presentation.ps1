<#
Creates the editable PPTX brief for the Synapse Campus Browser proposal.
It intentionally uses only .NET ZIP/XML primitives: the workstation has no PowerPoint
automation or presentation library, and the result must be reproducible offline.
#>
param(
  [string]$OutputPath = (Join-Path $PSScriptRoot '..\docs\Synapse-Campus-Browser-Ulyana.pptx')
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.IO.Compression.FileSystem

$out = [IO.Path]::GetFullPath($OutputPath)
$stage = Join-Path ([IO.Path]::GetTempPath()) ('synapse-pptx-' + [guid]::NewGuid())
New-Item -ItemType Directory -Path $stage -Force | Out-Null

function Write-Utf8([string]$Relative, [string]$Value) {
  $path = Join-Path $stage $Relative
  New-Item -ItemType Directory -Path (Split-Path $path) -Force | Out-Null
  [IO.File]::WriteAllText($path, $Value, [Text.UTF8Encoding]::new($false))
}
function X([string]$Value) { [Security.SecurityElement]::Escape($Value) }
function Color([string]$hex) { "<a:solidFill><a:srgbClr val=`"$hex`"/></a:solidFill>" }
function Shape([int]$Id,[string]$Name,[int]$X,[int]$Y,[int]$W,[int]$H,[string]$Fill,[string]$Kind='rect',[string]$Line='') {
  $lineXml = if ($Line) { "<a:ln w=`"12000`">$(Color $Line)</a:ln>" } else { '<a:ln><a:noFill/></a:ln>' }
  "<p:sp><p:nvSpPr><p:cNvPr id=`"$Id`" name=`"$(X $Name)`"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:spPr><a:xfrm><a:off x=`"$X`" y=`"$Y`"/><a:ext cx=`"$W`" cy=`"$H`"/></a:xfrm><a:prstGeom prst=`"$Kind`"><a:avLst/></a:prstGeom>$(Color $Fill)$lineXml</p:spPr><p:txBody><a:bodyPr/><a:lstStyle/><a:p/></p:txBody></p:sp>"
}
function Text([int]$Id,[string]$Name,[string]$Value,[int]$X,[int]$Y,[int]$W,[int]$H,[int]$Size,[string]$Hex='EAF8FB',[bool]$Bold=$false,[string]$Align='l') {
  $weight = if ($Bold) { ' b="1"' } else { '' }
  $paragraphs = ($Value -split "`n" | ForEach-Object { "<a:p><a:pPr algn=`"$Align`"/><a:r><a:rPr lang=`"ru-RU`" sz=`"$Size`"$weight>$(Color $Hex)<a:latin typeface=`"Aptos`"/></a:rPr><a:t>$(X $_)</a:t></a:r><a:endParaRPr lang=`"ru-RU`" sz=`"$Size`"/></a:p>" }) -join ''
  "<p:sp><p:nvSpPr><p:cNvPr id=`"$Id`" name=`"$(X $Name)`"/><p:cNvSpPr txBox=`"1`"/><p:nvPr/></p:nvSpPr><p:spPr><a:xfrm><a:off x=`"$X`" y=`"$Y`"/><a:ext cx=`"$W`" cy=`"$H`"/></a:xfrm><a:prstGeom prst=`"rect`"><a:avLst/></a:prstGeom><a:noFill/><a:ln><a:noFill/></a:ln></p:spPr><p:txBody><a:bodyPr wrap=`"square`" lIns=`"0`" rIns=`"0`" tIns=`"0`" bIns=`"0`"/><a:lstStyle/>$paragraphs</p:txBody></p:sp>"
}

$slides = @(
  @{ kicker='SYNAPSE × ВСГУТУ'; title='Campus Browser'; subtitle='Приватный учебный браузер\nна Rust + Tauri'; body='ТЗ и стартовая карта реализации для Ульяны'; tag='PILOT / WINDOWS 10–11' },
  @{ kicker='01 / ПРОДУКТОВАЯ ГРАНИЦА'; title='Не ещё один Chrome'; subtitle='А кампусный браузер, который не мешает учиться'; body='Обычные вкладки и настройки\n+ Журнал, расписание и Вектор\n+ privacy-first без рекламной телеметрии'; tag='ФОКУС: УЧЕБНЫЙ ДЕНЬ' },
  @{ kicker='02 / ВАЖНАЯ ЧЕСТНОСТЬ'; title='Tauri — не новый движок'; subtitle='На Windows он использует системный WebView2 / Chromium'; body='Нельзя обещать «в разы легче Chrome» для любого сайта.\nВыигрываем архитектурой: одна среда, выгрузка фона,\nнулевая телеметрия, без расширений и фоновых сервисов.'; tag='МЕТРИКИ, НЕ МАРКЕТИНГ' },
  @{ kicker='03 / РЕКОМЕНДУЕМЫЙ СТЕК'; title='Маленькая оболочка.\nСтрогие границы.'; subtitle='Rust берёт безопасность и жизненный цикл на себя'; body='TAURI v2 + RUST STABLE\nVUE 3 + TYPESCRIPT + PINIA + TAILWIND\nSQLCIPHER + WINDOWS DPAPI\nЛОКАЛЬНЫЙ ПОДПИСАННЫЙ BLOCKLIST'; tag='WINDOWS-FIRST MVP' },
  @{ kicker='04 / ПРИВАТНОСТЬ'; title='Данные студента —\nне расходный материал'; subtitle='Безопасность встроена до первой страницы'; body='• URL не уходит внешнему reputation-сервису\n• third-party cookies и трекеры блокируются\n• HTTPS-only без тихого downgrade\n• внешние сайты не получают Tauri IPC\n• Вектор видит только явно переданный контекст'; tag='152-ФЗ / PRIVACY-FIRST' },
  @{ kicker='05 / ПАМЯТЬ И СКОРОСТЬ'; title='Управляем вкладками,\nа не рисуем цифры'; subtitle='Цель — предсказуемая лёгкость на реальном ПК'; body='ОДИН WebView2 ENVIRONMENT\n≤ 2 активных рендера\nfreeze / discard неактивных вкладок\nlazy-load UI, нет polling и автопредзагрузки\nWPR + Process Explorer: median и p95'; tag='BENCHMARK VS EDGE + CHROME' },
  @{ kicker='06 / UX'; title='Привычно с первой минуты'; subtitle='Без перегруженного «универсального комбайна»'; body='Адресная строка · вкладки · назад/вперёд · загрузки\nИстория · избранное · масштаб · поиск по странице\nНастройки: приватность, HTTPS-only, доступность\nНовая вкладка: Журнал · Расписание · Вектор'; tag='KEYBOARD + 200% ZOOM' },
  @{ kicker='07 / GRADEBOOKAI'; title='Один визуальный язык.\nРазные контуры доверия.'; subtitle='Переносим только то, что безопасно переносить'; body='ПЕРЕНОСИМ: токены, i18n ru/en/zh, a11y, компоненты\nНЕ ПЕРЕНОСИМ: JWT, бизнес-правила, БД журнала\nЖурнал — доверенный origin с отдельной capability-политикой\nВектор не читает DOM чужих сайтов и историю.'; tag='NO SHARED SECRETS' },
  @{ kicker='08 / ПЛАН УЛЬЯНЫ'; title='18 рабочих дней\nдо защищённого PoC'; subtitle='Сначала фундамент, потом учебные модули'; body='A / 3 дня — architecture + threat model + shell\nB / 5 дней — вкладки, URL pipeline, HTTPS-only\nC / 5 дней — профиль, очистка, вкладки, benchmark\nD / 5 дней — расписание / Journal / Вектор на mock API'; tag='REVIEW: ЯРОСЛАВ · ВЛАД · АРИНА' },
  @{ kicker='09 / ГОТОВО К ПРИЁМКЕ'; title='Готово — это\nпроверяемо'; subtitle='Каждый критерий имеет тест или замер'; body='✓ внешняя страница не вызывает IPC\n✓ HTTP не открывается молча\n✓ в простое нет скрытой сети\n✓ test blocklist действительно блокирует\n✓ benchmark показывает дерево процессов\n✓ UI работает с клавиатуры и при 200%'; tag='SECURITY + UX + PERFORMANCE' },
  @{ kicker='SYNAPSE / NEXT'; title='Сделать браузер,\nкоторому можно доверить учебный день'; subtitle='Старт: отдельный репозиторий и PoC без боевых ПДн'; body='Полное ТЗ: docs/ULYANA-CAMPUS-BROWSER-SPEC.md\nТехническая основа: Tauri v2, WebView2, Rust\nРешения до старта: update endpoint, поисковик, профили,\nполитика Вектора и расписания.'; tag='LET’S BUILD THE RIGHT THING' }
)

$contentTypes = @('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>','<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/><Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/><Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/><Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/><Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>')
for($n=1;$n -le $slides.Count;$n++){ $contentTypes += "<Override PartName=`"/ppt/slides/slide$n.xml`" ContentType=`"application/vnd.openxmlformats-officedocument.presentationml.slide+xml`"/>" }
$contentTypes += '</Types>'
Write-Utf8 '[Content_Types].xml' ($contentTypes -join '')
Write-Utf8 '_rels/.rels' '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/><Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/></Relationships>'
Write-Utf8 'docProps/core.xml' '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><dc:title>Synapse Campus Browser — ТЗ для Ульяны</dc:title><dc:creator>Synapse</dc:creator><cp:lastModifiedBy>Synapse</cp:lastModifiedBy><dcterms:created xsi:type="dcterms:W3CDTF">2026-09-06T00:00:00Z</dcterms:created></cp:coreProperties>'
Write-Utf8 'docProps/app.xml' '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"><Application>Synapse offline generator</Application><Slides>11</Slides></Properties>'

$master = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><p:sldMaster xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld name="Synapse"><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr></p:spTree></p:cSld><p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"/><p:txStyles><p:titleStyle/><p:bodyStyle/><p:otherStyle/></p:txStyles></p:sldMaster>'
Write-Utf8 'ppt/slideMasters/slideMaster1.xml' $master
Write-Utf8 'ppt/slideMasters/_rels/slideMaster1.xml.rels' '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme" Target="../theme/theme1.xml"/></Relationships>'
Write-Utf8 'ppt/theme/theme1.xml' '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="Synapse"><a:themeElements><a:clrScheme name="Synapse"><a:dk1><a:srgbClr val="061D2B"/></a:dk1><a:lt1><a:srgbClr val="F4FBFC"/></a:lt1><a:dk2><a:srgbClr val="123547"/></a:dk2><a:lt2><a:srgbClr val="DDF4F6"/></a:lt2><a:accent1><a:srgbClr val="00AFC1"/></a:accent1><a:accent2><a:srgbClr val="78DCE5"/></a:accent2><a:accent3><a:srgbClr val="F2B544"/></a:accent3><a:accent4><a:srgbClr val="A4D9B6"/></a:accent4><a:accent5><a:srgbClr val="E785A1"/></a:accent5><a:accent6><a:srgbClr val="A693E6"/></a:accent6><a:hlink><a:srgbClr val="00AFC1"/></a:hlink><a:folHlink><a:srgbClr val="A693E6"/></a:folHlink></a:clrScheme><a:fontScheme name="Synapse"><a:majorFont><a:latin typeface="Aptos Display"/></a:majorFont><a:minorFont><a:latin typeface="Aptos"/></a:minorFont></a:fontScheme><a:fmtScheme name="Synapse"><a:fillStyleLst/><a:lnStyleLst/><a:effectStyleLst/><a:bgFillStyleLst/></a:fmtScheme></a:themeElements></a:theme>'

$slideRels = @('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">')
$slideIds = @()
for($n=1;$n -le $slides.Count;$n++){ $slideRels += "<Relationship Id=`"rId$($n+1)`" Type=`"http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide`" Target=`"slides/slide$n.xml`"/>"; $slideIds += "<p:sldId id=`"$(255+$n)`" r:id=`"rId$($n+1)`"/>" }
$slideRels += '</Relationships>'
Write-Utf8 'ppt/_rels/presentation.xml.rels' ($slideRels -join '')
Write-Utf8 'ppt/presentation.xml' ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><p:presentation xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst><p:sldIdLst>' + ($slideIds -join '') + '</p:sldIdLst><p:sldSz cx="12192000" cy="6858000" type="wide"/><p:notesSz cx="6858000" cy="9144000"/></p:presentation>')

for($n=1;$n -le $slides.Count;$n++) {
  $s = $slides[$n-1]; $id = 2
  $tree = @('<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>')
  $tree += Shape $id 'background' 0 0 12192000 6858000 '061D2B'; $id++
  $tree += Shape $id 'cyan ribbon' 0 0 330000 6858000 '00AFC1'; $id++
  $tree += Shape $id 'orb one' 10100000 300000 1580000 1580000 '123D52' 'ellipse'; $id++
  $tree += Shape $id 'orb two' 10900000 5100000 900000 900000 '00AFC1' 'ellipse'; $id++
  $tree += Shape $id 'hex mark' 8700000 480000 880000 880000 '00AFC1' 'hexagon'; $id++
  $tree += Text $id 'GB mark' 'GB' 8850000 700000 580000 340000 1800 '061D2B' $true 'ctr'; $id++
  $tree += Text $id 'kicker' $s.kicker 900000 680000 6800000 300000 1200 '78DCE5' $true; $id++
  $tree += Text $id 'title' $s.title 900000 1250000 7000000 1600000 3000 'F4FBFC' $true; $id++
  $tree += Text $id 'subtitle' $s.subtitle 900000 3000000 6900000 700000 1700 'A7C7D0' $false; $id++
  $tree += Shape $id 'body panel' 900000 4100000 8000000 1400000 '0E3042' 'roundRect' '1E6274'; $id++
  $tree += Text $id 'body' $s.body 1200000 4350000 7400000 1000000 1400 'EAF8FB'; $id++
  $tree += Shape $id 'tag pill' 900000 5950000 4000000 420000 '00AFC1' 'roundRect'; $id++
  $tree += Text $id 'tag' $s.tag 1120000 6070000 3600000 200000 1050 '061D2B' $true; $id++
  $tree += Text $id 'foot' ("SYNAPSE  /  $('{0:d2}' -f $n)") 9500000 6380000 1800000 200000 900 '78DCE5' $true 'r'; $id++
  $xml = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld><p:spTree>' + ($tree -join '') + '</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>'
  Write-Utf8 "ppt/slides/slide$n.xml" $xml
}

if(Test-Path $out){ Remove-Item -LiteralPath $out -Force }
[IO.Compression.ZipFile]::CreateFromDirectory($stage, $out)
Remove-Item -LiteralPath $stage -Recurse -Force
Write-Output "Created: $out"

import QtQuick

Rectangle {
    id: root
    property var result: ({})
    property bool busy: false
    signal addToken(string token)
    signal pickKind(string kind)

    readonly property string kind: String(result.kind || "")
    readonly property bool ambiguous: Boolean(result.ambiguous)
    readonly property string warning: String(result.warning || "")
    readonly property var ips: result.ips || []
    readonly property var domains: result.domains || []
    readonly property var peers: result.peers || []
    readonly property string token: String(result.token || "")
    readonly property string label: String(result.label || result.query || "")
    readonly property bool shared: Boolean(result.shared)

    visible: busy || Object.keys(result).length > 0
    height: visible ? col.implicitHeight + 16 : 0
    implicitHeight: height
    radius: 10
    color: T.surface2
    border.width: 1
    border.color: T.hairline
    clip: true

    Column {
        id: col
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.margins: 8
        spacing: 6

        Text {
            width: parent.width
            visible: root.busy
            text: "Смотрю адреса…"
            color: T.muted
            font.pixelSize: 11
            font.family: T.fontUi
        }

        Text {
            width: parent.width
            visible: !root.busy && root.kind === "domain" && root.ips.length
            text: root.ips.join("  ·  ")
            color: T.text
            font.pixelSize: 12
            font.family: T.fontMono
            wrapMode: Text.WordWrap
        }

        Text {
            width: parent.width
            visible: !root.busy && root.kind === "ip" && root.domains.length
            text: root.domains.join("  ·  ")
            color: T.text
            font.pixelSize: 12
            font.family: T.fontUi
            wrapMode: Text.WordWrap
        }

        Text {
            width: parent.width
            visible: !root.busy && (root.kind === "process" || root.kind === "service") && root.label.length > 0
            text: root.label
            color: T.text
            font.pixelSize: 11
            font.family: T.fontUi
            wrapMode: Text.WrapAtWordBoundaryOrAnywhere
        }

        Column {
            width: parent.width
            spacing: 4
            visible: !root.busy && root.peers.length > 0

            Text {
                text: "Сейчас ходит сюда"
                color: T.muted
                font.pixelSize: 10
                font.weight: Font.DemiBold
                font.family: T.fontUi
            }

            Repeater {
                model: root.peers
                delegate: Rectangle {
                    required property var modelData
                    width: col.width
                    implicitHeight: peerCol.implicitHeight + 10
                    radius: 8
                    color: T.rowBg
                    Column {
                        id: peerCol
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.leftMargin: 8
                        anchors.rightMargin: 8
                        spacing: 1
                        Text {
                            width: parent.width
                            text: modelData.ip + (modelData.port ? ":" + modelData.port : "")
                            color: T.text
                            font.pixelSize: 11
                            font.family: T.fontMono
                            elide: Text.ElideRight
                        }
                        Text {
                            visible: String(modelData.domain || "").length > 0
                            width: parent.width
                            text: modelData.domain
                            color: T.muted
                            font.pixelSize: 10
                            font.family: T.fontUi
                            elide: Text.ElideRight
                        }
                    }
                    MouseArea {
                        anchors.fill: parent
                        cursorShape: Qt.PointingHandCursor
                        onClicked: {
                            root.addToken(modelData.ip)
                            if (modelData.domain)
                                root.addToken(modelData.domain)
                        }
                    }
                }
            }
        }

        Text {
            width: parent.width
            visible: !root.busy && root.warning.length > 0
            text: root.warning
            color: T.warn
            font.pixelSize: 11
            font.family: T.fontUi
            wrapMode: Text.WordWrap
        }

        Flow {
            width: parent.width
            spacing: 6
            visible: !root.busy && (root.ambiguous || root.kind === "domain" || root.kind === "ip" || root.kind === "process" || root.kind === "service")

            Repeater {
                model: {
                    if (root.busy)
                        return []
                    var items = []
                    if (root.ambiguous) {
                        items.push({ label: "Как домен", token: root.label, kindPick: "domain" })
                        items.push({ label: "Как программа", token: "exe:" + root.label, kindPick: "process" })
                        return items
                    }
                    if (root.kind === "domain") {
                        if (root.token)
                            items.push({ label: "Домен", token: root.token })
                        for (var i = 0; i < root.ips.length; i++)
                            items.push({ label: root.ips[i], token: root.ips[i] })
                    } else if (root.kind === "ip") {
                        items.push({ label: "Адрес", token: root.token || root.label })
                        for (var d = 0; d < root.domains.length; d++)
                            items.push({ label: root.domains[d], token: root.domains[d] })
                    } else if (root.kind === "process" || root.kind === "service") {
                        if (root.token && !root.shared)
                            items.push({
                                label: root.kind === "service" ? "Служба" : "Программа",
                                token: root.token
                            })
                        for (var p = 0; p < root.ips.length; p++)
                            items.push({ label: root.ips[p], token: root.ips[p] })
                        for (var n = 0; n < root.domains.length; n++)
                            items.push({ label: root.domains[n], token: root.domains[n] })
                    } else if (root.token && !root.shared) {
                        items.push({ label: root.label || "Добавить", token: root.token })
                    }
                    return items
                }
                delegate: Rectangle {
                    required property var modelData
                    width: Math.max(0, Math.min(hintTxt.implicitWidth + 16, col.width))
                    height: Math.max(24, hintTxt.implicitHeight + 10)
                    radius: 8
                    color: T.accentSoft
                    clip: true
                    Text {
                        id: hintTxt
                        x: 8
                        anchors.verticalCenter: parent.verticalCenter
                        width: Math.max(0, Math.min(implicitWidth, col.width - 16))
                        text: modelData.label
                        color: T.accent
                        font.pixelSize: 11
                        font.family: T.fontUi
                        wrapMode: Text.WrapAnywhere
                        elide: Text.ElideMiddle
                        maximumLineCount: 2
                    }
                    MouseArea {
                        anchors.fill: parent
                        cursorShape: Qt.PointingHandCursor
                        onClicked: {
                            if (modelData.kindPick)
                                root.pickKind(modelData.kindPick)
                            root.addToken(modelData.token)
                        }
                    }
                }
            }
        }
    }
}

import QtQuick
import "Theme.js" as T

Column {
    id: root
    property string label: ""
    property bool on: false
    property bool controlEnabled: true
    signal toggled(bool on)
    width: parent ? parent.width : 0
    spacing: 6

    Text {
        text: root.label
        color: T.muted
        font.pixelSize: 11
        font.weight: Font.Medium
        font.family: T.fontUi
        visible: root.label !== ""
    }
    Segmented {
        width: parent.width
        enabled: root.controlEnabled
        value: root.on ? "1" : "0"
        model: [
            { label: "Выкл", value: "0" },
            { label: "Вкл", value: "1" }
        ]
        onActivated: (v) => root.toggled(v === "1")
    }
}

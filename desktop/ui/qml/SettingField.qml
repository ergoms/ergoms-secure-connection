import QtQuick
import QtQuick.Controls
import "Theme.js" as T

Column {
    id: root
    property string label: ""
    property string settingKey: ""
    property bool password: false
    spacing: 6
    opacity: enabled ? 1 : 0.45
    width: parent ? parent.width : 280

    Text {
        text: root.label
        color: T.muted
        font.pixelSize: 11
        font.family: T.fontUi
    }

    TextField {
        id: field
        width: parent.width
        height: 40
        text: String(bridge.settings[root.settingKey] ?? "")
        echoMode: root.password ? TextInput.Password : TextInput.Normal
        color: T.text
        selectedTextColor: T.text
        selectionColor: T.select
        font.pixelSize: 13
        font.family: T.fontUi
        leftPadding: 12
        rightPadding: 12
        readOnly: !root.enabled
        onTextEdited: if (root.enabled) bridge.settings[root.settingKey] = text
        background: Rectangle {
            color: T.surface2
            radius: 10
            border.width: field.activeFocus ? 1 : 0
            border.color: T.accent
        }
    }
}

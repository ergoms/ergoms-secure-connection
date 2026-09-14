import QtQuick

Column {
    id: root
    property var fields: []
    property bool showAdvanced: true
    width: parent ? parent.width : 0
    spacing: 10

    Repeater {
        model: root.fields
        SettingField {
            required property var modelData
            visible: !modelData.advanced || root.showAdvanced
            height: visible ? implicitHeight : 0
            width: parent.width
            label: modelData.label || ""
            settingKey: modelData.key || ""
            password: Boolean(modelData.password)
        }
    }
}

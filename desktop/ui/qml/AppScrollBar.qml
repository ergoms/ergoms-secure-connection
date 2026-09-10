import QtQuick
import QtQuick.Controls
import "Theme.js" as T

ScrollBar {
    id: control
    policy: ScrollBar.AsNeeded
    implicitWidth: 10
    implicitHeight: 10
    padding: 3
    minimumSize: 0.08

    contentItem: Rectangle {
        implicitWidth: 3
        implicitHeight: 3
        radius: 1.5
        color: control.pressed ? "#d5dbe6" : (control.hovered ? "#9aa3b4" : "#5c6678")
        opacity: control.size < 1.0 && (control.active || control.hovered || control.pressed) ? 1 : 0
        Behavior on opacity { NumberAnimation { duration: 160 } }
        Behavior on color { ColorAnimation { duration: 120 } }
    }

    background: Item {
        implicitWidth: 10
        implicitHeight: 10
    }
}

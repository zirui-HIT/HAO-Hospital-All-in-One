# 递归遍历./utils下的.xml.xml文件并删除中间的.xml
find ./utils -name "*.xml.xml" -exec bash -c 'mv "$0" "${0/.xml.xml/.xml}"' {} \;
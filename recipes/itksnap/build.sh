#!/usr/bin/env bash
set -e

export toolName='itksnap'
export toolVersion='4.0.2'

if [ "$1" != "" ]; then
    echo "Entering Debug mode"
    export debug=$1
fi

source ../main_setup.sh

neurodocker generate ${neurodocker_buildMode} \
   --base-image ubuntu:22.04 \
   --pkg-manager apt \
   --run="printf '#!/bin/bash\nls -la' > /usr/bin/ll" \
   --run="chmod +x /usr/bin/ll" \
   --run="mkdir -p ${mountPointList}" \
   --install curl ca-certificates unzip libqt5gui5 libopengl0 \
   --run="curl -fsSL -o /example_data.zip https://www.nitrc.org/frs/download.php/750/MRI-crop.zip  \
         && unzip /example_data.zip \
         && rm /example_data.zip" \
   --workdir /opt/${toolName}-${toolVersion} \
   --env QT_QPA_PLATFORM="xcb" \
   --run="curl -fsSL --retry 5 https://ixpeering.dl.sourceforge.net/project/itk-snap/itk-snap/${toolVersion}/itksnap-${toolVersion}-Linux-gcc64.tar.gz | tar -xz --strip-components=1 -C /opt/${toolName}-${toolVersion}" \
   --env DEPLOY_PATH=/opt/${toolName}-${toolVersion}/bin/ \
   --env PATH='$PATH':/opt/${toolName}-${toolVersion}/bin/ \
   --copy README.md /README.md \
  > ${imageName}.${neurodocker_buildExt}
   # --entrypoint "/opt/${toolName}-${toolVersion}/bin/itksnap /MRIcrop-orig.gipl" \

if [ "$1" != "" ]; then
   ./../main_build.sh
fi


   

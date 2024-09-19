#!/usr/bin/env bash
set -e

export toolName='samsrfx'
export toolVersion='0.0.1'

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
   --matlabmcr version=2023b method=binaries \
   --workdir /opt/${toolName}-${toolVersion}/ \
   --install wget openjdk-8-jre \
   --run="wget --no-check-certificate --progress=bar:force -P /opt/${toolName}-${toolVersion}/ https://object-store.rc.nectar.org.au/v1/AUTH_dead991e1fa847e3afcca2d3a7041f5d/build/SamSrfX.zip \
      && unzip -q SamSrfX.zip -d /opt/${toolName}-${toolVersion}/ \
      && chmod a+x /opt/${toolName}-${toolVersion}/run_SamSrfX.sh \
      && chmod a+x /opt/${toolName}-${toolVersion}/SamSrfX \
      && rm -f SamSrfX.zip" \
   --env DEPLOY_BINS=SamSrfX \
   --env PATH=/opt/${toolName}-${toolVersion}/:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
   --copy README.md /README.md \
  > ${toolName}_${toolVersion}.Dockerfile

if [ "$1" != "" ]; then
   ./../main_build.sh
fi

# 
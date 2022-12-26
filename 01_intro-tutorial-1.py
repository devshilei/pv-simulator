# -*- coding: utf-8 -*-

# --------------------------------
# Name:         01_intro-tutorial.py
# Author:       devshilei@gmail.com
# @Time         2022/12/1 10:45
# Description:  
# --------------------------------

import pvlib
import pandas as pd
import matplotlib.pyplot as plt

# 需要对比的四个坐标点（分别是纬度、经度、地区名称、海拔与时区）
coordinates = [
    (32.2, -111.0, "Tucson", 700, "Etc/GMT+7"),
    (35.1, -106.6, "Albuquerque", 1500, "Etc/GMT+7"),
    (37.8, -122.4, "San Francisco", 10, "Etc/GMT+8"),
    (52.5, 13.4, "Berlin", 34, "Etc/GMT-1"),
]

# 定义光伏模组
sandia_modules = pvlib.pvsystem.retrieve_sam("SandiaMod")
module = sandia_modules["Canadian_Solar_CS5P_220M___2009_"]

# 定义逆变器
sapm_inverters = pvlib.pvsystem.retrieve_sam("cecinverter")
inverter = sapm_inverters["ABB__MICRO_0_25_I_OUTD_US_208__208V_"]

# 定义温度模型
temperature_model_parameters = pvlib.temperature.TEMPERATURE_MODEL_PARAMETERS["sapm"]["open_rack_glass_glass"]

# 获取天气参数（根据经纬度信息从 pvgis 中获取天气数据）
tmys = []
for location in coordinates:
    latitude, longitude, name, altitude, timezone = location
    weather = pvlib.iotools.get_pvgis_tmy(latitude, longitude,
                                          map_variables=True)[0]
    weather.index.name = "utc_time"
    tmys.append(weather)

# 定义光伏系统（光伏模组、逆变器、方位角：180朝南）
system = {"module": module, "inverter": inverter, "surface_azimuth": 180}

energies = {}

for location, weather in zip(coordinates, tmys):
    latitude, longitude, name, altitude, timezone = location

    # 设定光伏面板斜角与纬度相同
    system["surface_tilt"] = latitude

    # 通过海拔高度计算大气压
    # 公式：【100 * ((44331.514 - altitude) / 11880.516) ** (1 / 0.1902632)】
    pressure = pvlib.atmosphere.alt2pres(altitude)

    # 计算太阳位置
    solpos = pvlib.solarposition.get_solarposition(
        time=weather.index,
        latitude=latitude,
        longitude=longitude,
        altitude=altitude,
        temperature=weather["temp_air"],
        pressure=pressure
    )

    # 计算辐照度（根据一年中的某一天来确定地外辐射）
    # 根据（时区\时间）、
    #    （太阳常数；日辐射常数[默认1366.1]）、
    #    （计算ET辐射的方法，支持["pyephem", "spencer", "asce", "nrel"]）、
    #    （历元轨道年日记数）
    dni_extra = pvlib.irradiance.get_extra_radiation(weather.index)

    # 计算海平面上的相对气团(未经压力调整)。—— 获得相对气团，根据太阳顶点计算
    # 计算方法包括 simple 割线(视天顶角)
    # kasten1966            Fritz Kasten. "A New Table and Approximation Formula for the Relative Optical Air Mass".
    # youngirvine1967       A. T. Young and W. M. Irvine, "Multicolor Photoelectric Photometry of the Brighter Planets,"
    # kastenyoung1989       Fritz Kasten and Andrew Young. "Revised optical air mass tables and approximation formula".
    # gueymard1993          C. Gueymard, "Critical analysis and performance assessment of clear sky solar irradiance
    #                       models using theoretical and measured data,"
    # young1994             A. T. Young, "AIR-MASS AND REFRACTION,"
    # pickering2002         Keith A. Pickering. "The Ancient Star Catalog".
    airmass = pvlib.atmosphere.get_relative_airmass(solpos["apparent_zenith"], model="kastenyoung1989")

    # 根据相对气团和压强计算绝对气团
    am_abs = pvlib.atmosphere.get_absolute_airmass(airmass, pressure)

    # 计算太阳在地表的入射角(即太阳向量和地表之间的夹角)。
    aoi = pvlib.irradiance.aoi(
        system["surface_tilt"],
        system["surface_azimuth"],
        solpos["apparent_zenith"],
        solpos["azimuth"]
    )

    # 确定面内总辐照度及其光束、天空散射和地面反射组件，使用指定的天空漫射辐照度模型。
    total_irradiance = pvlib.irradiance.get_total_irradiance(
        system["surface_tilt"],     # 面板斜角
        system["surface_azimuth"],  # 面板方位角
        solpos["apparent_zenith"],  # 太阳天顶角
        solpos["azimuth"],          # 太阳方位角
        weather["dni"],             # 法向直接日射辐照度(标准直射辐照度)
        weather["ghi"],             # 全球水平辐照度
        weather["dhi"],             # 水平散射辐照度
        dni_extra=dni_extra,        # 辐照度
        model="haydavies"           # 天空漫射模型，包括:isotropic(default)\klucher\haydavies\reindl\king\perez
    )

    # 根据桑迪亚阵列性能模型计算电池温度
    cell_temperature = pvlib.temperature.sapm_cell(
        total_irradiance["poa_global"],  # 总入射辐照度
        weather["temp_air"],             # 环境干球温度
        weather["wind_speed"],           # 10米高风速
        **temperature_model_parameters   # 温度模型参数
    )

    # 有效辐照度
    # 利用 SAPM 光谱计算 SAPM 有效辐照度损失和 SAPM 入射角损失函数。
    effective_irradiance = pvlib.pvsystem.sapm_effective_irradiance(
        total_irradiance["poa_direct"],   # 照射在模块上的直接辐照度
        total_irradiance["poa_diffuse"],  # 漫射照射在模块上
        am_abs,                           # 绝对的气团
        aoi,                              # 太阳入射角
        module                            # 光伏模块
    )

    # 假设在电池温度为25℃（最佳转换效果）的情况下在面板中取5个点生成I-V曲线（根据：Sandia光伏阵列性能模型）
    dc = pvlib.pvsystem.sapm(effective_irradiance, cell_temperature, module)

    # 使用 Sandia 的并网光伏逆变器模型将直流电源和电压转换为交流电源。
    # v_mp 最大功率点电压(V)
    # p_mp 最大功率(W)
    ac = pvlib.inverter.sandia(dc["v_mp"], dc["p_mp"], inverter)

    # 求和
    annual_energy = ac.sum()

    # 设定各个地区名称一年来产生的电量
    energies[name] = annual_energy

# 定义 matplot 数据以及显示图
energies = pd.Series(energies)
energies.plot(kind="bar", rot=0)
plt.rcParams["font.sans-serif"] = ["SimHei"]
plt.rcParams["axes.unicode_minus"] = False
plt.title("各地光伏年产电量预测")
plt.xlabel("地区")
plt.ylabel("年发电总量预测 (W hr)")
plt.show()
plt.savefig("img/pv_ac_forecast.png")
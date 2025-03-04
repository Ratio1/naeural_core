# Ratio1 Core Packages (formerly Ratio1 Edge Protocol Core Modules)

Welcome to the **Ratio1 Core packages** repository, previously known as the **Ratio1 Edge Protocol Core Modules**. These core packages are the foundational elements of the Ratio1 ecosystem, designed to enhance the protocol and drive the development of the Ratio1 Edge Node through ongoing research and community contributions. This README provides an overview of the core functionalities, components, and guidance on how to integrate the Ratio1 Core Packages into your projects.

## Overview

The **Ratio1 Core packages** are engineered to facilitate the rapid advancement and deployment of AI applications at the edge within the Ratio1 ecosystem. These core modules underpin several key functionalities essential for building robust edge computing solutions and enhancing the overall protocol:

- **Data Collection**: Acquire data through various methods, including:
  - **Default Plugins**: MQTT, RTSP, CSV, ODBC
  - **Custom-Built Plugins**: Integration with sensors and other specialized data sources

- **Data Processing**: Transform and process collected data to prepare it for trustless model training and inference, ensuring data integrity and reliability.

- **Model Training and Inference**: Utilize plugins to train AI models and perform trustless inference tasks, leveraging decentralized resources for enhanced performance and security.

- **Post-Inference Business Logic**: Execute business logic after inference to derive actionable insights and make informed decisions based on AI outputs.

- **Pipeline Persistence**: Maintain the persistence of pipelines to ensure reliability and reproducibility of AI workflows across deployments.

- **Communication**: Enable seamless communication through both MQ-based and API-based methods, including advanced routing and load balancing via ngrok for optimized network performance.

These modules serve as the core for implementing edge nodes within the Ratio1 ecosystem or integrating seamlessly into third-party Web2 applications, providing flexibility and scalability for diverse use cases. The primary objective of the Ratio1 Core Packages is to enhance the protocol and ecosystem, thereby improving the functionality and performance of the Ratio1 Edge Node through dedicated research and community-driven contributions.

## Features

- **Modular Design**: Easily extend functionality with custom plugins for data collection, processing, and more, allowing for tailored solutions to meet specific application needs.
- **Scalability**: Designed to scale from small edge devices to large-scale deployments, ensuring consistent performance regardless of deployment size.
- **Interoperability**: Compatible with a wide range of data sources and communication protocols, facilitating integration with existing systems and technologies.
- **Ease of Integration**: The core packages are intended to be integrated as components within the Ratio1 Edge Node or third-party edge node execution engines, rather than standalone applications.

## Contributing

We welcome contributions from the community to help enhance the Ratio1 Core Packages. Your contributions play a vital role in advancing the Ratio1 ecosystem and improving the Ratio1 Edge Node. 

## Installation

The Ratio1 Core Packages are not intended for standalone use. Instead, they are designed to be integrated as components within the Ratio1 Edge Node or utilized by third-party edge node execution engines. For detailed integration instructions, please refer to the documentation provided within the Ratio1 Edge Node repository or contact our support team for assistance.

## License

This project is licensed under the **Apache 2.0 License**. For more details, please refer to the [LICENSE](LICENSE) file.

## Contact

For more information, visit our website at [https://ratio1.ai](https://ratio1.ai) or reach out to us via email at [support@ratio1.ai](mailto:support@ratio1.ai).

## Project Financing Disclaimer

This project incorporates open-source components developed with the support of financing grants **SMIS 143488** and **SMIS 156084**, provided by the Romanian Competitiveness Operational Programme. We extend our sincere gratitude for this support, which has been instrumental in advancing our work and enabling us to share these resources with the community.

The content and information within this repository are solely the responsibility of the authors and do not necessarily reflect the views of the funding agencies. The grants have specifically supported certain aspects of this open-source project, facilitating broader dissemination and collaborative development.

For any inquiries regarding the funding and its impact on this project, please contact the authors directly.

## Citation

If you use the Ratio1 Core Packages in your research or projects, please cite them as follows:

```bibtex
@misc{Ratio1CorePackages,
  author       = {Ratio1.AI},
  title        = {Ratio1 Core Packages},
  year         = {2024-2025},
  howpublished = {\url{https://github.com/Ratio1/naeural_core}},
}
```


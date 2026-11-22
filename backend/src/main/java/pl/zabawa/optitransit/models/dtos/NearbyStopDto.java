package pl.zabawa.optitransit.models.dtos;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class NearbyStopDto {

    private String id;
    private String name;
    private String code;
    private String platformCode;
    private Double latitude;
    private Double longitude;
    private String townName;
    private Integer wheelchairBoarding;
    private Double distanceMeters;
}
